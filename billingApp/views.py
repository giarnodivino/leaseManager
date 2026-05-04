import csv

from django.shortcuts import render, redirect, get_object_or_404
from .models import Tenant, Building, Lease, BillingRecord, Account, Units, Payment
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import HttpResponse
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from django.db.models import Exists, OuterRef, Sum
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction

def get_available_units(building_id):
    leased_units = Lease.objects.filter(buildingName_id=building_id, pastLease=False).values_list("unitID_id", flat=True)
    available_units = Units.objects.filter(building_id=building_id).exclude(pk__in=leased_units)
    return available_units

def calculate_total_outstanding():
    total_outstanding = Decimal("0.00")

    billing_records = BillingRecord.objects.filter(
        status=BillingRecord.STATUS_UNPAID
    )

    for record in billing_records:
        total_outstanding += record.balance or Decimal("0.00")

    return f"{total_outstanding:,.2f}"

# total revenue should include all paid and unpaid bills
def calculate_total_revenue():
    total_revenue = Decimal("0.00")

    lease_records = Lease.objects.all()

    for record in lease_records:
        total_revenue += (record.rentAmount or Decimal("0.00"))
        total_revenue += (record.parkingFees or Decimal("0.00"))
        total_revenue += (record.signageFees or Decimal("0.00"))


    return f"{total_revenue:,.2f}"

def money(value):
    return (value or Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_bill_total_due(bill):
    return money(bill.amountDue) + money(bill.penaltyFee)

def recalculate_bill_balance(bill):
    """
    Use this when bill amount or penalty changes.

    This does NOT push anything to the next bill.
    It only recalculates the bill's current status based on payments.
    """

    total_paid = Payment.objects.filter(billingID=bill).aggregate(
        total=Sum("amountPaid")
    )["total"] or Decimal("0.00")

    total_paid = money(total_paid)
    total_due = get_bill_total_due(bill)

    if total_paid <= Decimal("0.00"):
        bill.status = BillingRecord.STATUS_UNPAID
        bill.balance = total_due

    elif total_paid < total_due:
        bill.status = BillingRecord.STATUS_UNDERPAID
        bill.balance = Decimal("0.00")

    elif total_paid == total_due:
        bill.status = BillingRecord.STATUS_PAID
        bill.balance = Decimal("0.00")

    else:
        bill.status = BillingRecord.STATUS_OVERPAID
        bill.balance = Decimal("0.00")

    bill.save(update_fields=["status", "balance"])


def settle_bill_and_push_difference_to_next_bill(bill):

    tenant = bill.tenant

    total_paid = Payment.objects.filter(billingID=bill).aggregate(
        total=Sum("amountPaid")
    )["total"] or Decimal("0.00")

    total_paid = money(total_paid)
    total_due = get_bill_total_due(bill)

    previous_adjustment = money(bill.carryoverAdjustment)

    tenant.carryover_balance = money(tenant.carryover_balance) - previous_adjustment

    if total_paid <= Decimal("0.00"):
        bill.status = BillingRecord.STATUS_UNPAID
        bill.balance = total_due
        bill.carryoverAdjustment = Decimal("0.00")

    elif total_paid < total_due:
        shortage = total_due - total_paid

        bill.status = BillingRecord.STATUS_UNDERPAID
        bill.balance = Decimal("0.00")

        # Negative means amount to add to next bill.
        bill.carryoverAdjustment = -shortage
        tenant.carryover_balance = money(tenant.carryover_balance) - shortage

    elif total_paid == total_due:
        bill.status = BillingRecord.STATUS_PAID
        bill.balance = Decimal("0.00")

        bill.carryoverAdjustment = Decimal("0.00")

    else:
        excess = total_paid - total_due

        bill.status = BillingRecord.STATUS_OVERPAID
        bill.balance = Decimal("0.00")

        # Positive means credit to subtract from next bill.
        bill.carryoverAdjustment = excess
        tenant.carryover_balance = money(tenant.carryover_balance) + excess

    tenant.save(update_fields=["carryover_balance"])
    bill.save(update_fields=["status", "balance", "carryoverAdjustment"])

def calculate_total_paid():
    total_paid = Decimal("0.00")
    payments = Payment.objects.all()
    for payment in payments:
        total_paid += payment.amountPaid or Decimal("0.00")
    return f"{total_paid:,.2f}"

def mark_carryover_bills_as_paid_if_settled(tenant):
    """
    If tenant carryover is already zero, the previous underpayment/overpayment
    was already absorbed into the next bill.

    Therefore, old UNDERPAID / OVERPAID bills should now show as PAID.
    """

    tenant.refresh_from_db()

    if money(tenant.carryover_balance) != Decimal("0.00"):
        return

    BillingRecord.objects.filter(
        tenant=tenant,
        status__in=[
            BillingRecord.STATUS_UNDERPAID,
            BillingRecord.STATUS_OVERPAID,
        ],
    ).update(
        status=BillingRecord.STATUS_PAID,
        balance=Decimal("0.00"),
    )

def mark_carryover_bills_as_paid_if_settled(tenant):
    """
    If tenant.carryover_balance is already zero, it means the old
    underpayment/overpayment has already been absorbed into a new bill.

    So the old UNDERPAID / OVERPAID bills should now show as PAID.
    """

    tenant.refresh_from_db()

    if money(tenant.carryover_balance) != Decimal("0.00"):
        return

    BillingRecord.objects.filter(
        tenant=tenant,
        status__in=[
            BillingRecord.STATUS_UNDERPAID,
            BillingRecord.STATUS_OVERPAID,
        ],
    ).update(
        status=BillingRecord.STATUS_PAID,
        balance=Decimal("0.00"),
    )


def get_date_today():
    from datetime import datetime
    return datetime.now().strftime("%B %d, %Y")

def lease_has_pending_bills(lease):
    return BillingRecord.objects.filter(
        lease=lease,
        status=BillingRecord.STATUS_UNPAID
    ).exists()

def get_logged_in_account(request):
    if not request.user.is_authenticated:
        return None

    username = (request.user.username or "").strip()
    if not username:
        return None

    account = Account.objects.filter(username=username).first()
    if account:
        return account

    account = Account.objects.filter(username__iexact=username).first()
    if account:
        return account

    return Account.objects.create(
        firstName=request.user.first_name or "",
        lastName=request.user.last_name or "",
        username=username,
        password="AUTO_SYNC",
    )


def get_tenant_display_name(tenant):
    if not tenant:
        return ""

    company_name = (tenant.companyName or "").strip()
    return company_name or tenant.contactPerson


def create_csv_response(filename):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")
    return response


def apply_report_filters(queryset, request, lease_prefix="lease", tenant_prefix="tenant"):
    building_filter = request.GET.get("building")
    lease_status_filter = request.GET.get("lease_status")

    if building_filter:
        queryset = queryset.filter(**{f"{lease_prefix}__buildingName_id": building_filter})

    if lease_status_filter == "active":
        queryset = queryset.filter(**{f"{lease_prefix}__pastLease": False})
    elif lease_status_filter == "none":
        queryset = queryset.exclude(
            **{f"{tenant_prefix}__lease__pastLease": False}
        )

    return queryset


def register_admin(request):
    if request.method == "POST":
        firstName = request.POST.get("firstName")
        lastName = request.POST.get("lastName")
        username = request.POST.get("username")
        password = request.POST.get("password")
        confirmPassword = request.POST.get("confirm_password")

        if password == confirmPassword:
            if not Account.objects.filter(username=username).exists():
                Account.objects.create(firstName=firstName, lastName=lastName, username=username, password=password)
                user = User.objects.create_user(first_name=firstName, last_name=lastName, username=username, password=password)
                return redirect("login_page")
            else:
                messages.error(request, "Username already exists.")
                return render(request, "billingApp/register_admin.html")
        else:
            messages.error(request, "Passwords do not match.")
            return render(request, "billingApp/register_admin.html")

    return render(request, "billingApp/register_admin.html")

def login_page(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect("home_page")
        else:
            messages.error(request, "Invalid username or password.")
            return render(request, "billingApp/login_page.html")

    return render(request, "billingApp/login_page.html")

@login_required
def logout_view(request):
    logout(request)
    return redirect("login_page")

@login_required
def home_page(request):
    total_outstanding_balance = calculate_total_outstanding()
    total_revenue = calculate_total_revenue()
    total_paid = calculate_total_paid()

    unpaid_clients_query = (
    BillingRecord.objects
    .filter(status=BillingRecord.STATUS_UNPAID)
    .values(
        "tenant",
        "tenant__companyName",
        "tenant__contactPerson",
    )
    .annotate(total_unpaid=Sum("balance"))
    .order_by("tenant__companyName", "tenant__contactPerson")[:10]
)

    unpaid_clients = []

    for item in unpaid_clients_query:
        tenant = Tenant.objects.get(pk=item["tenant"])

        tenant_name = (
            item["tenant__companyName"] or ""
        ).strip() or item["tenant__contactPerson"]

        unpaid_clients.append({
            "tenant": tenant,
            "name": tenant_name,
            "amount": f"{item['total_unpaid'] or Decimal('0.00'):,.2f}",
        })

    unpaid_client_count = BillingRecord.objects.filter(
        status=BillingRecord.STATUS_UNPAID
    ).values("tenant").distinct().count()

    buildings = Building.objects.all().order_by("buildingName")

    building_cards = []

    for building in buildings:
        active_tenant_count = Lease.objects.filter(
            buildingName=building,
            pastLease=False
        ).values("tenantName").distinct().count()

        total_units = Units.objects.filter(
            building=building
        ).count()

        occupied_units = Lease.objects.filter(
            buildingName=building,
            pastLease=False
        ).values("unitID").distinct().count()

        available_slots = total_units - occupied_units

        building_cards.append({
            "building": building,
            "active_tenant_count": active_tenant_count,
            "available_slots": available_slots,
            "available_parking_slots": building.parkingCapacity or 0,
            "available_signage_slots": building.signageCapacity or 0,
        })

    return render(
        request,
        "billingApp/home_page.html",
        {
            "total_outstanding": total_outstanding_balance,
            "total_revenue": total_revenue,
            "total_paid": total_paid,
            "unpaid_clients": unpaid_clients,
            "unpaid_client_count": unpaid_client_count,
            "building_cards": building_cards,
        }
    )

@login_required
def buildings_main(request):
    building_objects = Building.objects.all().order_by('buildingName')

    for b in building_objects:
        b.unit_count = Units.objects.filter(building_id=b.pk).count()
        b.available_units = get_available_units(b.pk).count()

    date_today = get_date_today()
    return render(request, 'billingApp/buildings_main.html', {'buildings':building_objects, 'date_today': date_today})

@login_required
def tenants_main(request):

    building_filter = request.GET.get("building")
    lease_status_filter = request.GET.get("lease_status")

    tenant_objects = Tenant.objects.all().order_by('companyName', 'contactPerson')
    active_leases = Lease.objects.filter(pastLease=False).select_related('tenantName', 'buildingName')


    if building_filter:
        tenant_objects = tenant_objects.filter(
            lease__buildingName_id=building_filter,
            lease__pastLease=False
        ).distinct()


    if lease_status_filter == "active":
        tenant_objects = tenant_objects.filter(lease__pastLease=False).distinct()

    elif lease_status_filter == "none":
        tenant_objects = tenant_objects.exclude(lease__pastLease=False).distinct()

    lease_by_tenant_id = {lease.tenantName_id: lease for lease in active_leases}

    for t in tenant_objects:

        if not (t.companyName or "").strip():
            t.companyName_display = t.contactPerson
        else:
            t.companyName_display = t.companyName

        active_lease = lease_by_tenant_id.get(t.id)

        if active_lease:
            t.buildingName_display = active_lease.buildingName.buildingName
        else:
            t.buildingName_display = "No active lease"

    buildings = Building.objects.all()

    date_today = get_date_today()

    return render(
        request,
        'billingApp/tenants_main.html',
        {
            'tenants': tenant_objects,
            'date_today': date_today,
            'buildings': buildings,
            'selected_building': building_filter,
            'selected_lease_status': lease_status_filter,
        }
    )

@login_required
def add_building(request):
    if(request.method=="POST"):
        buildingName = request.POST.get('buildingName')
        buildingAddress = request.POST.get('buildingAddress')
        signageCapacity = request.POST.get('signageCapacity')
        parkingCapacity = request.POST.get('parkingCapacity')
        admin_account = get_logged_in_account(request)
        if not Building.objects.filter(buildingName=buildingName).exists():
            Building.objects.create(buildingName=buildingName, buildingAddress=buildingAddress, 
                                    signageCapacity=signageCapacity, 
                                    parkingCapacity=parkingCapacity,
                                    modified_by=admin_account)
        else:
            return redirect('add_building')
        return redirect('buildings_main')
    else:
        return render(request, 'billingApp/add_building.html')

@login_required
def building_details(request, pk):
    building = get_object_or_404(Building, pk=pk)

    building.unit_count = Units.objects.filter(building_id=building.pk).count()

    date_today = get_date_today()
    return render(request, 'billingApp/building_details.html', {'b':building, 'date_today': date_today})

@login_required
def delete_building(request, pk):
    b = get_object_or_404(Building, pk=pk)
    
    if Lease.objects.filter(buildingName=b, pastLease=False).exists():
        messages.error(request, "Cannot delete this building because it has active leases.")
        return redirect("building_details", pk=b.pk)

    Building.objects.filter(pk=pk).delete()
    return redirect('buildings_main')

@login_required
def add_tenant(request):
    if(request.method=="POST"):
        companyName = (request.POST.get('companyName') or "").strip()
        contactPerson = request.POST.get('contactPerson')
        phoneNumber = request.POST.get('phoneNumber')
        email = request.POST.get('email')
        admin_account = get_logged_in_account(request)

        if companyName:
            if Tenant.objects.filter(companyName=companyName).exists():
                messages.error(request, "A tenant with this company name already exists.")
                return redirect('add_tenant')
        else:
            companyName = None

        if companyName and Tenant.objects.filter(companyName__iexact=companyName).exists():
            messages.error(request, "A tenant with this company name already exists.")
            return redirect("add_tenant")

        if contactPerson and Tenant.objects.filter(contactPerson__iexact=contactPerson).exists():
            messages.error(request, "A tenant with this contact person name already exists.")
            return redirect("add_tenant")

        Tenant.objects.create(companyName=companyName, contactPerson=contactPerson, 
                                phoneNumber=phoneNumber, email=email,
                                modified_by=admin_account)
        return redirect('tenants_main')
    return render(request, 'billingApp/add_tenant.html', {})

@login_required
def tenant_details(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    leases = (
        Lease.objects
        .filter(tenantName=tenant, pastLease=False)
        .select_related("buildingName", "unitID")
        .order_by("contractStart", "id")
    )

    if not (tenant.companyName or "").strip():
        tenant.companyName_display = tenant.contactPerson
    else:
        tenant.companyName_display = tenant.companyName
        
    date_today = get_date_today()
    return render(request, 'billingApp/tenant_details.html', {'t': tenant, 'leases': leases, 'date_today': date_today})

@login_required
def past_leases(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    leases = (
        Lease.objects
        .filter(tenantName=tenant, pastLease=True)
        .select_related("buildingName", "unitID")
        .order_by("-archived_date", "-contractEnd", "-contractStart", "-id")
    )

    if not (tenant.companyName or "").strip():
        tenant.companyName_display = tenant.contactPerson
    else:
        tenant.companyName_display = tenant.companyName

    return render(request, "billingApp/past_leases.html", {"t": tenant, "leases": leases})

@login_required
def send_reminder_email(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    
    # Get pending bills for this tenant
    pending_bills = BillingRecord.objects.filter(
        tenant=tenant,
        status__in=[
            BillingRecord.STATUS_UNPAID,
            BillingRecord.STATUS_UNDERPAID
        ]
    )
    
    if not pending_bills.exists():
        messages.info(request, "No pending bills to remind about.")
        return redirect('tenant_details', pk=tenant_id)
    
    # Calculate total amount due
    total_due = sum(bill.balance or Decimal('0.00') for bill in pending_bills)
    
    # Compose email
    subject = f"Billing Reminder - Outstanding Payment Due"
    message = f"""Dear {tenant.contactPerson},

This is a friendly reminder that you have outstanding payment(s) due.

Total Amount Due: ₱{total_due:,.2f}
Contact Person: {tenant.contactPerson}
Phone: {tenant.phoneNumber}

Please settle your account at your earliest convenience.

Best regards,
J&F Divino Development Corporation"""
    
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [tenant.email],
            fail_silently=False,
        )
        messages.success(request, f"Reminder email sent to {tenant.email}")
    except Exception as e:
        messages.error(request, f"Failed to send email: {str(e)}")
    
    return redirect('tenant_details', pk=tenant_id)

@login_required
def delete_tenant(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)

    has_pending_bills = BillingRecord.objects.filter(
        tenant=tenant,
        status=BillingRecord.STATUS_UNPAID
    ).exists()

    if has_pending_bills:
        messages.error(
            request,
            "Cannot delete this tenant because they still have unpaid or partially paid bills."
        )
        return redirect("tenant_details", pk=tenant.pk)

    tenant.delete()
    messages.success(request, "Tenant deleted successfully.")
    return redirect("tenants_main")


@login_required
def add_lease(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    tenant_objects = Tenant.objects.all()
    building_objects = Building.objects.all().order_by("buildingName")
    active_leased_unit_ids = Lease.objects.filter(pastLease=False).values_list("unitID_id", flat=True)
    units = (
        Units.objects.select_related("building")
        .exclude(pk__in=active_leased_unit_ids)
        .order_by("building__buildingName", "unitID")
    )
    units_payload = [
        {
            "id": unit.id,
            "unitID": unit.unitID,
            "building_id": unit.building_id,
        }
        for unit in units
    ]

    if request.method == "POST":
        building_id = request.POST.get("building_id")
        unitID = request.POST.get("unitID")
        rentAmount = request.POST.get("rentAmount")
        contractLength = request.POST.get("contractLength")
        contractStart = request.POST.get("contractStart")
        signageFees = request.POST.get("signageFees")
        parkingFees = request.POST.get("parkingFees")
        lease_attachment = request.FILES.get("leaseAttachment")
        admin_account = get_logged_in_account(request)

        rentAmount = float(rentAmount) if rentAmount else 0.0
        vatAmount = float(rentAmount * 0.12)
        signageFees = float(signageFees) if signageFees else None
        parkingFees = float(parkingFees) if parkingFees else None
        contractLength = int(contractLength) if contractLength else None

        if contractStart and contractLength:
            from datetime import datetime, timedelta
            contractStartDate = datetime.strptime(contractStart, "%Y-%m-%d").date()
            contractEndDate = contractStartDate + timedelta(days=contractLength*30)
        else:            
            contractEndDate = None

        if not building_id or not unitID:
            messages.error(request, "Please select both a building and a unit.")
            return redirect("add_lease", pk=tenant.pk)

        selected_building = Building.objects.filter(pk=building_id).first()
        selected_unit = Units.objects.filter(pk=unitID, building_id=building_id).first()

        if not selected_building or not selected_unit:
            messages.error(request, "Selected unit does not belong to the chosen building.")
            return redirect("add_lease", pk=tenant.pk)

        if not contractStart or not contractLength:
            messages.error(request, "Please provide both contract start date and contract length.")
            return redirect("add_lease", pk=tenant.pk)

        occupied_lease = Lease.objects.filter(
            buildingName=selected_building,
            unitID=selected_unit,
            pastLease=False,
        ).first()

        if occupied_lease:
            occupied_tenant = occupied_lease.tenantName
            occupied_tenant_name = (occupied_tenant.companyName or "").strip() or occupied_tenant.contactPerson
            messages.error(
                request,
                f"Unit {selected_unit.unitID} in {selected_building.buildingName} is already taken by {occupied_tenant_name}.",
            )
            return redirect("add_lease", pk=tenant.pk)

        if lease_attachment:
            allowed_types = ["application/pdf", "image/jpeg", "image/png", "image/webp"]

            if lease_attachment.content_type not in allowed_types:
                messages.error(request, "Invalid file type. Please upload a PDF or image file.")
                return redirect("add_lease", pk=tenant.pk)

        Lease.objects.create(
            buildingName=selected_building,
            tenantName=tenant,
            modified_by=admin_account,
            unitID=selected_unit,
            rentAmount=rentAmount,
            vatAmount=vatAmount,
            contractLength=contractLength,
            contractStart=contractStart,
            contractEnd=contractEndDate,
            pastLease=False,
            signageFees=signageFees,
            parkingFees=parkingFees,
            leaseAttachment=lease_attachment,
        )
        return redirect("tenant_details", pk=tenant.pk)

    return render(
        request,
        "billingApp/add_lease.html",
        {
            "t": tenant,
            "tenants": tenant_objects,
            "buildings": building_objects,
            "units_payload": units_payload,
        },
    )

@login_required
def delete_lease(request, pk):
    lease = get_object_or_404(Lease, pk=pk)
    tenant_pk = lease.tenantName_id

    if not lease.pastLease and lease_has_pending_bills(lease):
        messages.error(
            request,
            "Cannot delete this active lease because it still has unpaid or partially paid bills."
        )
        return redirect("tenant_details", pk=tenant_pk)

    lease.delete()
    messages.success(request, "Lease deleted successfully.")
    return redirect("tenant_details", pk=tenant_pk)

@login_required
def billing_records_main(request):

    building_filter = request.GET.get("building")
    lease_status_filter = request.GET.get("lease_status")

    tenants = Tenant.objects.all().order_by('companyName', 'contactPerson')
    leases = Lease.objects.all()

    active_leases = Lease.objects.filter(
        pastLease=False
    ).select_related('tenantName', 'buildingName')


    if building_filter:
        tenants = tenants.filter(
            lease__buildingName_id=building_filter,
            lease__pastLease=False
        ).distinct()


    if lease_status_filter == "active":
        tenants = tenants.filter(lease__pastLease=False).distinct()

    elif lease_status_filter == "none":
        tenants = tenants.exclude(lease__pastLease=False).distinct()


    lease_by_tenant_id = {lease.tenantName_id: lease for lease in active_leases}

    for t in tenants:

        if not (t.companyName or "").strip():
            t.companyName_display = t.contactPerson
        else:
            t.companyName_display = t.companyName

        lease = lease_by_tenant_id.get(t.id)

        if lease:
            t.buildingName_display = lease.buildingName.buildingName
        else:
            t.buildingName_display = "No active lease"

    buildings = Building.objects.all()

    date_today = get_date_today()

    return render(
        request,
        'billingApp/billing_records_main.html',
        {
            'tenants': tenants,
            'leases': leases,
            'buildings': buildings,
            'selected_building': building_filter,
            'selected_lease_status': lease_status_filter,
            'date_today': date_today
        }
    )


@login_required
def export_bills_report(request):
    bills = BillingRecord.objects.select_related(
        "tenant",
        "lease",
        "lease__buildingName",
        "lease__unitID",
    ).order_by("-dateIssued", "-id")

    bills = apply_report_filters(bills, request)

    response = create_csv_response("bills_report.csv")
    writer = csv.writer(response)
    writer.writerow([
        "Bill No",
        "Tenant",
        "Contact Person",
        "Building",
        "Unit",
        "Billing For",
        "Date Issued",
        "Date Due",
        "Amount Due",
        "Penalty Fee",
        "Total Due",
        "Balance",
        "Carryover Adjustment",
        "Status",
    ])

    for bill in bills:
        writer.writerow([
            bill.billing_number(),
            get_tenant_display_name(bill.tenant),
            bill.tenant.contactPerson if bill.tenant else "",
            bill.lease.buildingName.buildingName if bill.lease_id else "",
            bill.lease.unitID.unitID if bill.lease_id and bill.lease.unitID_id else "",
            bill.get_billingFor_display(),
            bill.dateIssued,
            bill.dateDue,
            money(bill.amountDue),
            money(bill.penaltyFee),
            get_bill_total_due(bill),
            money(bill.balance),
            money(bill.carryoverAdjustment),
            bill.get_status_display(),
        ])

    return response

@login_required
def view_bills(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)

    mark_carryover_bills_as_paid_if_settled(tenant)

    bills = BillingRecord.objects.filter(
        tenant=tenant
    ).order_by("-id")

    payments = Payment.objects.filter(
        tenantID=tenant
    )

    total_unpaid_balance = Decimal("0.00")

    carryover_balance = money(tenant.carryover_balance)

    carryover_type = None
    carryover_amount = Decimal("0.00")
    carryover_message = None

    if carryover_balance > Decimal("0.00"):
        carryover_type = "OVERPAYMENT"
        carryover_amount = carryover_balance
        carryover_message = f"Tenant has an overpayment credit of ₱ {carryover_amount:,.2f}. This will be deducted from the next bill."

    elif carryover_balance < Decimal("0.00"):
        carryover_type = "UNDERPAYMENT"
        carryover_amount = abs(carryover_balance)
        carryover_message = f"Tenant has an underpayment balance of ₱ {carryover_amount:,.2f}. This will be added to the next bill."

    if not (tenant.companyName or "").strip():
        tenant.companyName_display = tenant.contactPerson
    else:
        tenant.companyName_display = tenant.companyName

    for b in bills:
        b.payment = Payment.objects.filter(billingID=b).order_by("-id").first()

        total_bill = get_bill_total_due(b)
        b.total_bill_display = f"{total_bill:,.2f}"

        if b.status == BillingRecord.STATUS_UNPAID:
            b.balance_display = f"{total_bill:,.2f}"
            total_unpaid_balance += total_bill
        else:
            b.balance_display = "0.00"

    has_active_lease = Lease.objects.filter(
        tenantName=tenant,
        pastLease=False
    ).exists()

    active_lease = Lease.objects.filter(
        tenantName=tenant,
        pastLease=False
    ).first()

    date_today = get_date_today()

    return render(
        request,
        "billingApp/view_bills.html",
        {
            "tenant": tenant,
            "bills": bills,
            "payments": payments,
            "has_active_lease": has_active_lease,
            "active_lease": active_lease,
            "date_today": date_today,
            "total_unpaid_balance": f"{total_unpaid_balance:,.2f}",

            "carryover_type": carryover_type,
            "carryover_amount": f"{carryover_amount:,.2f}",
            "carryover_message": carryover_message,
        },
    )

@login_required
def add_bill(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)

    leases = (
        Lease.objects
        .filter(tenantName=tenant, pastLease=False)
        .select_related("buildingName", "unitID")
        .order_by("contractStart", "id")
    )
    primary_lease = leases.first()

    if not primary_lease:
        messages.error(request, "This tenant does not have an active lease.")
        return redirect("view_bills", pk=tenant.pk)

    rent_total = sum(money(l.rentAmount) for l in leases)
    parking_total = sum(money(l.parkingFees) for l in leases)
    signage_total = sum(money(l.signageFees) for l in leases)
    combined_rent_total = rent_total + parking_total + signage_total

    if request.method == "POST":
        billing_for = (request.POST.get("billingFor") or "").upper()
        date_issued = request.POST.get("dateIssued")
        amountdue_raw = request.POST.get("payable")
        admin_account = get_logged_in_account(request)

        if not billing_for or not date_issued:
            messages.error(request, "Please fill in all required fields.")
            return redirect("add_bill", pk=tenant.pk)

        if billing_for == BillingRecord.RENT:
            base_amount = combined_rent_total
        else:
            amountdue_raw = (amountdue_raw or "0").replace(",", "")
            base_amount = money(Decimal(amountdue_raw))

        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)

            carryover = money(tenant.carryover_balance)

            # Positive carryover = credit, subtract from bill.
            # Negative carryover = due, add to bill.
            adjusted_amount = money(base_amount - carryover)

            if adjusted_amount <= Decimal("0.00"):
                amountdue = Decimal("0.00")

                # Credit is bigger than this bill.
                # Keep remaining credit for future bills.
                tenant.carryover_balance = money(-adjusted_amount)

            else:
                amountdue = adjusted_amount

                # Carryover fully consumed.
                tenant.carryover_balance = Decimal("0.00")

            tenant.save(update_fields=["carryover_balance"])

            mark_carryover_bills_as_paid_if_settled(tenant)

            duplicate_bill = BillingRecord.objects.filter(
                tenant=tenant,
                lease=primary_lease,
                dateIssued=date_issued,
                billingFor=billing_for,
                amountDue=amountdue,
            ).exists()

            if duplicate_bill:
                messages.error(
                    request,
                    "Duplicate bill detected. A bill with the same date, amount, and billing type already exists."
                )
                return redirect("add_bill", pk=tenant.pk)

            bill = BillingRecord.objects.create(
                tenant=tenant,
                lease=primary_lease,
                modified_by=admin_account,
                dateIssued=date_issued,
                billingFor=billing_for,
                amountDue=amountdue,
                balance=amountdue,
            )

            if amountdue == Decimal("0.00"):
                bill.status = BillingRecord.STATUS_PAID
                bill.balance = Decimal("0.00")
                bill.save(update_fields=["status", "balance"])

        return redirect("view_bills", pk=tenant.pk)

    return render(
        request,
        "billingApp/add_bill.html",
        {
            "tenant": tenant,
            "lease": primary_lease,
            "leases": leases,
            "rent_total": rent_total,
            "parking_total": parking_total,
            "signage_total": signage_total,
            "combined_rent_total": combined_rent_total,
        }
    )

@login_required
def add_units(request, pk):
    building_details = get_object_or_404(Building, pk=pk)
    if request.method == "POST":
        building_id = building_details.pk
        add_mode = request.POST.get("add_mode", "single")

        if add_mode == "bulk":
            try:
                floor = int(request.POST.get("floor", ""))
                start_room = int(request.POST.get("start_room", ""))
                end_room = int(request.POST.get("end_room", ""))
            except ValueError:
                messages.error(request, "Please provide a valid floor and room range.")
                return redirect("add_unit", pk=building_id)

            if not (1 <= floor <= 9 and 1 <= start_room <= end_room <= 99):
                messages.error(request, "Floor must be 1-9 and room numbers must be 1-99.")
                return redirect("add_unit", pk=building_id)

            unit_numbers = [
                floor * 100 + room
                for room in range(start_room, end_room + 1)
            ]

            if len(unit_numbers) > 500:
                messages.error(request, "Please add 500 units or fewer at a time.")
                return redirect("add_unit", pk=building_id)

            existing_unit_numbers = set(
                Units.objects.filter(building_id=building_id, unitID__in=unit_numbers)
                .values_list("unitID", flat=True)
            )
            units_to_create = [
                Units(building_id=building_id, unitID=unit_number)
                for unit_number in unit_numbers
                if unit_number not in existing_unit_numbers
            ]

            if units_to_create:
                Units.objects.bulk_create(units_to_create)

            skipped_count = len(existing_unit_numbers)
            created_count = len(units_to_create)

            if created_count and skipped_count:
                messages.success(
                    request,
                    f"{created_count} units added successfully. {skipped_count} existing units were skipped.",
                )
            elif created_count:
                messages.success(request, f"{created_count} units added successfully.")
            else:
                messages.warning(request, "No units were added because all generated units already exist.")

            return redirect("add_unit", pk=building_id)

        unit_number = request.POST.get("unit_number")

        try:
            unit_number = int(unit_number)
        except (TypeError, ValueError):
            messages.error(request, "Please provide a valid unit number.")
            return redirect("add_unit", pk=building_id)

        if not 100 <= unit_number <= 999:
            messages.error(request, "Unit number must be a 3-digit number.")
            return redirect("add_unit", pk=building_id)

        existing_unit = Units.objects.filter(building_id=building_id, unitID=unit_number).first()
        if existing_unit:
            messages.error(request, "This unit already exists.")
            return redirect("add_unit", pk=building_id)

        Units.objects.create(building_id=building_id, unitID=unit_number)
        messages.success(request, "Unit added successfully.")
        return redirect("add_unit", pk=building_id)

    buildings = Building.objects.all()

    date_today = get_date_today()
    return render(request, "billingApp/add_unit.html", {"buildings": buildings, "building": building_details, "date_today": date_today})


@login_required
def view_units(request, pk):
    building = get_object_or_404(Building, pk=pk)
    active_leases = Lease.objects.filter(unitID=OuterRef("pk"), pastLease=False)
    units = (
        Units.objects.filter(building=building)
        .annotate(is_occupied=Exists(active_leases))
        .order_by("unitID")
    )
    date_today = get_date_today()
    return render(request, "billingApp/view_units.html", {"building": building, "units": units, "date_today": date_today})


@login_required
@require_POST
def delete_unit(request, pk):
    unit = get_object_or_404(Units, pk=pk)
    building_pk = unit.building_id

    if Lease.objects.filter(unitID=unit, pastLease=False).exists():
        messages.error(request, "Cannot delete this unit because it is currently occupied.")
        return redirect("view_units", pk=building_pk)

    unit.delete()
    messages.success(request, "Unit deleted successfully.")
    return redirect("view_units", pk=building_pk)


@login_required
def payments_main(request):
    building_filter = request.GET.get("building")
    lease_status_filter = request.GET.get("lease_status")

    tenants = Tenant.objects.all().order_by('companyName', 'contactPerson')
    leases = Lease.objects.all()
    active_leases = Lease.objects.filter(pastLease=False).select_related('tenantName', 'buildingName')


    if building_filter:
        tenants = tenants.filter(
            lease__buildingName_id=building_filter,
            lease__pastLease=False
        ).distinct()


    if lease_status_filter == "active":
        tenants = tenants.filter(lease__pastLease=False).distinct()
    elif lease_status_filter == "none":
        tenants = tenants.exclude(lease__pastLease=False).distinct()


    lease_by_tenant_id = {lease.tenantName_id: lease for lease in active_leases}

    for t in tenants:
        if not (t.companyName or "").strip():
            t.companyName_display = t.contactPerson
        else:
            t.companyName_display = t.companyName

        lease = lease_by_tenant_id.get(t.id)
        t.buildingName_display = lease.buildingName.buildingName if lease else "No active lease"

    buildings = Building.objects.all()
    date_today = get_date_today()

    return render(
        request,
        "billingApp/payments_main.html",
        {
            "tenants": tenants,
            "leases": leases,
            "buildings": buildings,
            "selected_building": building_filter,
            "selected_lease_status": lease_status_filter,
            "date_today": date_today,
        }
    )


@login_required
def export_payments_report(request):
    payments = Payment.objects.select_related(
        "tenantID",
        "billingID",
        "billingID__lease",
        "billingID__lease__buildingName",
        "billingID__lease__unitID",
    ).order_by("-datePaid", "-id")

    payments = apply_report_filters(
        payments,
        request,
        lease_prefix="billingID__lease",
        tenant_prefix="tenantID",
    )

    response = create_csv_response("payments_report.csv")
    writer = csv.writer(response)
    writer.writerow([
        "Payment ID",
        "Reference Number",
        "Tenant",
        "Contact Person",
        "Building",
        "Unit",
        "Bill No",
        "Billing For",
        "Date Paid",
        "Amount Paid",
        "Payment Method",
    ])

    for payment in payments:
        bill = payment.billingID
        lease = bill.lease if bill and bill.lease_id else None

        writer.writerow([
            payment.id,
            payment.referenceNumber,
            get_tenant_display_name(payment.tenantID),
            payment.tenantID.contactPerson if payment.tenantID else "",
            lease.buildingName.buildingName if lease else "",
            lease.unitID.unitID if lease and lease.unitID_id else "",
            bill.billing_number() if bill else "",
            bill.get_billingFor_display() if bill else "",
            payment.datePaid,
            money(payment.amountPaid),
            payment.get_paymentMethod_display() if payment.paymentMethod else "",
        ])

    return response

def view_payments(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    payments = Payment.objects.filter(tenantID=tenant).order_by("-id")
    # get the bill for each payment and save the billingFor value to payment object for access to it in template
    for payment in payments:
        bill = BillingRecord.objects.filter(id=payment.billingID_id).first()
        payment.billingFor = bill.billingFor if bill else "N/A"
        # save the billingFor and name it subAccountName
        payment.subAccountName = bill.billingFor if bill else "N/A"

    if not (tenant.companyName or "").strip():
        tenant.companyName_display = tenant.contactPerson
    else:
        tenant.companyName_display = tenant.companyName

    return render(request, 'billingApp/view_payments.html', {"tenant": tenant, "payments": payments})

from decimal import Decimal, ROUND_HALF_UP
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from .models import Tenant, BillingRecord

@login_required
def soa(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    active_leases = list(
        Lease.objects
        .filter(tenantName=tenant, pastLease=False)
        .select_related("buildingName", "unitID")
        .order_by("-contractStart", "-id")
    )
    lease = active_leases[0] if active_leases else None

    if not lease:
        messages.error(request, "This tenant does not have an active lease. Unable to generate SOA.")
        return redirect("view_bills", pk=tenant.pk)

    bills_qs = BillingRecord.objects.filter(
        tenant=tenant,
        status=BillingRecord.STATUS_UNPAID,
    ).order_by("dateIssued", "id")

    ids = request.GET.get("ids")
    if ids:
        id_list = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
        bills_qs = bills_qs.filter(id__in=id_list)

    lines = []
    grand_total = Decimal("0.00")

    for b in bills_qs:
        amount = (b.balance or Decimal("0.00")).quantize(Decimal("0.01"))
        penalty_amount = money(b.penaltyFee)
        principal_amount = money(amount - penalty_amount)
        if principal_amount < Decimal("0.00"):
            principal_amount = Decimal("0.00")

        bill_no = f"BL-{b.id:06d}"
        
        if b.billingFor == BillingRecord.RENT:
            rent_amount = sum(
                (lease_item.rentAmount or Decimal("0.00"))
                for lease_item in active_leases
            ).quantize(Decimal("0.01"))
            parking_fees = sum(
                (lease_item.parkingFees or Decimal("0.00"))
                for lease_item in active_leases
            ).quantize(Decimal("0.01"))
            signage_fees = sum(
                (lease_item.signageFees or Decimal("0.00"))
                for lease_item in active_leases
            ).quantize(Decimal("0.01"))
            
            # Derive VAT as 12% of rent amount
            vat_amount = (rent_amount * Decimal("0.12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            # Base rent = rent amount - derived VAT
            base_rent = (rent_amount - vat_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            
            # Calculate total original amount
            original_total = base_rent + vat_amount + parking_fees + signage_fees
            
            # Calculate scale factor for remaining balance
            if original_total > Decimal("0.00"):
                scale = principal_amount / original_total
            else:
                scale = Decimal("1.00")
            
            # Scale each component
            scaled_base_rent = (base_rent * scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            scaled_vat = (vat_amount * scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            scaled_parking = (parking_fees * scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            scaled_signage = (signage_fees * scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            
            rent_total = scaled_base_rent + scaled_vat
            split_total = rent_total + scaled_signage + scaled_parking
            rounding_adjustment = principal_amount - split_total

            if signage_fees == Decimal("0.00") and parking_fees == Decimal("0.00"):
                scaled_base_rent += rounding_adjustment
                rent_total += rounding_adjustment
            
            lines.append({
                "no": bill_no,
                "date": b.dateIssued,
                "particulars": "Rent",
                "amount": scaled_base_rent,
                "vat": scaled_vat,
                "total": rent_total,
                "amount_display": f"{scaled_base_rent:,.2f}",
                "vat_display": f"{scaled_vat:,.2f}",
                "total_display": f"{rent_total:,.2f}",
            })

            if signage_fees > Decimal("0.00"):
                signage_total = scaled_signage
                if parking_fees == Decimal("0.00"):
                    signage_total += rounding_adjustment

                lines.append({
                    "no": bill_no,
                    "date": b.dateIssued,
                    "particulars": "Signage",
                    "amount": signage_total,
                    "vat": Decimal("0.00"),
                    "total": signage_total,
                    "amount_display": f"{signage_total:,.2f}",
                    "vat_display": "0.00",
                    "total_display": f"{signage_total:,.2f}",
                })

            if parking_fees > Decimal("0.00"):
                parking_total = scaled_parking + rounding_adjustment

                lines.append({
                    "no": bill_no,
                    "date": b.dateIssued,
                    "particulars": "Parking",
                    "amount": parking_total,
                    "vat": Decimal("0.00"),
                    "total": parking_total,
                    "amount_display": f"{parking_total:,.2f}",
                    "vat_display": "0.00",
                    "total_display": f"{parking_total:,.2f}",
                })
            
            if penalty_amount > Decimal("0.00"):
                lines.append({
                    "no": bill_no,
                    "date": b.dateIssued,
                    "particulars": "Penalty",
                    "amount": penalty_amount,
                    "vat": Decimal("0.00"),
                    "total": penalty_amount,
                    "amount_display": f"{penalty_amount:,.2f}",
                    "vat_display": "0.00",
                    "total_display": f"{penalty_amount:,.2f}",
                })

            grand_total += amount
            
        else:
            # For non-rent bills
            total = principal_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            grand_total += amount

            particulars = ""
            if b.billingFor == BillingRecord.ELECTRICITY:
                particulars = "Electric"
            elif b.billingFor == BillingRecord.WATER:
                particulars = "Water"

            lines.append({
                "no": bill_no,
                "date": b.dateIssued,
                "particulars": particulars,
                "amount": amount,
                "vat": Decimal("0.00"),
                "total": total,
                "amount_display": f"{amount:,.2f}",
                "vat_display": "0.00",
                "total_display": f"{total:,.2f}",
            })

            if penalty_amount > Decimal("0.00"):
                lines.append({
                    "no": bill_no,
                    "date": b.dateIssued,
                    "particulars": "Penalty",
                    "amount": penalty_amount,
                    "vat": Decimal("0.00"),
                    "total": penalty_amount,
                    "amount_display": f"{penalty_amount:,.2f}",
                    "vat_display": "0.00",
                    "total_display": f"{penalty_amount:,.2f}",
                })

    carryover_balance = money(tenant.carryover_balance)
    if carryover_balance != Decimal("0.00"):
        if carryover_balance > Decimal("0.00"):
            carryover_total = -carryover_balance
            particulars = "Overpayment Credit"
        else:
            carryover_total = abs(carryover_balance)
            particulars = "Underpayment Debit"

        lines.append({
            "no": "",
            "date": None,
            "particulars": particulars,
            "amount": carryover_total,
            "vat": Decimal("0.00"),
            "total": carryover_total,
            "amount_display": f"{carryover_total:,.2f}",
            "vat_display": "0.00",
            "total_display": f"{carryover_total:,.2f}",
        })
        grand_total += carryover_total

    company_display = (tenant.companyName or "").strip() or tenant.contactPerson
    
    current_admin = get_logged_in_account(request)
    prepared_by_name = ""
    if current_admin and current_admin.firstName and current_admin.lastName:
        prepared_by_name = f"{current_admin.firstName} {current_admin.lastName}".strip()
    elif current_admin:
        # Fallback to username if names aren't set
        prepared_by_name = current_admin.username or ""

    context = {
        "tenant": tenant,
        "company_display": company_display,
        "building_address": lease.buildingName.buildingAddress,
        "unit_id": lease.unitID.unitID,
        "lines": lines,
        "grand_total": grand_total.quantize(Decimal("0.01")),
        "grand_total_display": f"{grand_total:,.2f}",

        "deposit_account_number": "BDO 001498023822",
        "deposit_account_name": "J&F Divino Development Corporation",
        "company_tin": "001-461-259-00000",
        "prepared_by_name": prepared_by_name,
    }
    return render(request, "billingApp/soa.html", context)


@login_required
def add_payment(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)

    lease = (
        Lease.objects
        .filter(tenantName=tenant, pastLease=False)
        .select_related("buildingName")
        .order_by("-contractStart", "-id")
        .first()
    )

    if not lease:
        messages.error(request, "This tenant does not have an active lease.")
        return redirect("view_payments", pk=tenant.pk)

    bills = BillingRecord.objects.filter(
        tenant=tenant,
        status=BillingRecord.STATUS_UNPAID,
    ).order_by("dateIssued", "id")

    if not (tenant.companyName or "").strip():
        tenant.companyName_display = tenant.contactPerson
    else:
        tenant.companyName_display = tenant.companyName

    if lease.rentAmount:
        lease.rentAmount = f"{lease.rentAmount:,.2f}"

    if lease.parkingFees:
        lease.parkingFees = f"{lease.parkingFees:,.2f}"
    else:
        lease.parkingFees = "0.00"

    if lease.signageFees:
        lease.signageFees = f"{lease.signageFees:,.2f}"
    else:
        lease.signageFees = "0.00"

    for bill in bills:
        bill.display_id = f"BL-{bill.id:06d}"
        bill.display_label = "Due"
        bill.display_amount = get_bill_total_due(bill)

    if request.method == "POST":
        billing_id = request.POST.get("bill_id")
        amount_paid_raw = request.POST.get("amountPaid")
        sub_account_name = request.POST.get("subAccountName")
        date_paid = request.POST.get("datePaid")
        payment_method = request.POST.get("paymentMethod")
        reference_number = (request.POST.get("referenceNumber") or "").strip()
        proof_of_payment = request.FILES.get("proofOfPayment")
        admin_account = get_logged_in_account(request)

        if not (billing_id and amount_paid_raw and date_paid and payment_method and reference_number):
            messages.error(request, "Please fill in all required fields.")
            return redirect("add_payment", pk=tenant.pk)

        if Payment.objects.filter(referenceNumber=reference_number).exists():
            messages.error(request, "This reference number has already been used for another payment.")
            return redirect("add_payment", pk=tenant.pk)

        try:
            amount_paid = money(Decimal(str(amount_paid_raw).replace(",", "")))
        except Exception:
            messages.error(request, "Amount paid is invalid.")
            return redirect("add_payment", pk=tenant.pk)

        if amount_paid <= Decimal("0.00"):
            messages.error(request, "Amount paid must be greater than zero.")
            return redirect("add_payment", pk=tenant.pk)

        try:
            billing_id = int(billing_id)
        except Exception:
            messages.error(request, "Selected bill is invalid.")
            return redirect("add_payment", pk=tenant.pk)

        bill_to_pay = bills.filter(pk=billing_id).first()

        if not bill_to_pay:
            messages.error(request, "Selected bill is invalid.")
            return redirect("add_payment", pk=tenant.pk)

        try:
            with transaction.atomic():
                Payment.objects.create(
                    tenantID=tenant,
                    modified_by=admin_account,
                    amountPaid=amount_paid,
                    subAccountName=sub_account_name or None,
                    datePaid=date_paid,
                    billingID=bill_to_pay,
                    referenceNumber=reference_number,
                    paymentMethod=payment_method,
                    proofOfPayment=proof_of_payment,
                )

                settle_bill_and_push_difference_to_next_bill(bill_to_pay)

            messages.success(request, "Payment added successfully.")
            return redirect("view_payments", pk=tenant.pk)

        except Exception as e:
            messages.error(request, f"Unable to save payment: {str(e)}")
            return redirect("add_payment", pk=tenant.pk)

    return render(
        request,
        "billingApp/add_payment.html",
        {
            "tenant": tenant,
            "lease": lease,
            "bills": bills,
        }
    )


@login_required
def delete_payment(request, pk):
    payment = get_object_or_404(Payment, pk=pk)

    tenant = payment.tenantID
    bill = payment.billingID

    with transaction.atomic():
        payment.delete()
        settle_bill_and_push_difference_to_next_bill(bill)

    messages.success(request, "Payment deleted successfully.")
    return redirect("view_bills", pk=tenant.pk)

def delete_bill(request, pk):
    bill = get_object_or_404(BillingRecord, pk=pk)
    tenant_pk = bill.tenant_id
    bill.delete()
    return redirect("view_bills", pk=tenant_pk)

# get a specific tenant's specific bill and allow editing of the bill's amount due and date issued, but only if the bill is not fully paid. If the bill is fully paid, show an error message that the bill cannot be edited.
@login_required
def edit_bill(request, pk):
    bill = get_object_or_404(BillingRecord, pk=pk)
    tenant = bill.tenant
    date_today = get_date_today()

    if bill.status != BillingRecord.STATUS_UNPAID:
        messages.error(request, "Cannot edit a bill that already has a payment record.")
        return redirect("view_bills", pk=tenant.pk)

    if request.method == "POST":
        date_issued = request.POST.get("date_issued")
        due_date = request.POST.get("due_date")
        amount = request.POST.get("amount")
        particulars = request.POST.get("particulars")
        admin_account = get_logged_in_account(request)

        try:
            if not date_issued or not due_date:
                messages.error(request, "Please fill in all required fields.")
                return render(request, "billingApp/edit_bill.html", {
                    "tenant": tenant,
                    "bill": bill,
                    "date_today": date_today
                })

            # Always allow dateIssued and dateDue to be edited
            bill.dateIssued = datetime.strptime(date_issued, "%Y-%m-%d").date()
            bill.dateDue = datetime.strptime(due_date, "%Y-%m-%d").date()
            bill.modified_by = admin_account

            # If RENT, only update dates
            if bill.billingFor == BillingRecord.RENT:
                bill.save(update_fields=["dateIssued", "dateDue", "modified_by"])
                messages.success(request, "Rent bill dates updated successfully.")
                return redirect("view_bills", pk=tenant.pk)

            # Non-rent bills require amount and particulars
            if not amount or not particulars:
                messages.error(request, "Please fill in all required fields.")
                return render(request, "billingApp/edit_bill.html", {
                    "tenant": tenant,
                    "bill": bill,
                    "date_today": date_today
                })

            # Prevent changing electricity/water into rent
            if particulars == BillingRecord.RENT:
                messages.error(request, "Cannot change billing type to Rent.")
                return render(request, "billingApp/edit_bill.html", {
                    "tenant": tenant,
                    "bill": bill,
                    "date_today": date_today
                })

            new_amount = Decimal(amount)

            bill.amountDue = new_amount
            bill.balance = new_amount
            bill.billingFor = particulars

            bill.save()

            messages.success(request, "Bill updated successfully.")
            return redirect("view_bills", pk=tenant.pk)

        except Exception as e:
            messages.error(request, f"Error updating bill: {str(e)}")
            return render(request, "billingApp/edit_bill.html", {
                "tenant": tenant,
                "bill": bill,
                "date_today": date_today
            })

    return render(request, "billingApp/edit_bill.html", {
        "tenant": tenant,
        "bill": bill,
        "date_today": date_today
    })

def view_payment_details(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    tenant = payment.tenantID
    bill = payment.billingID

    return render(request, "billingApp/payment_details.html", {"tenant": tenant, "payment": payment, "bill": bill})

def view_proof_of_payment(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    tenant = payment.tenantID
    date_today = get_date_today()
    
    return render(request, "billingApp/view_proof_of_payment.html", {"payment": payment, "tenant": tenant, "date_today": date_today})

@login_required
def renew_lease(request, pk):
    """
    Renew/edit an active lease.
    Archives the current lease and creates a new lease with updated terms.
    """

    lease = get_object_or_404(
        Lease.objects.select_related("tenantName", "buildingName", "unitID"),
        pk=pk,
        pastLease=False,
    )
    tenant = lease.tenantName

    if lease_has_pending_bills(lease):
        messages.error(
            request,
            "Cannot renew/archive this lease because it still has unpaid bills."
        )
        return redirect("tenant_details", pk=tenant.pk)

    building = lease.buildingName
    unit = lease.unitID
    date_today = get_date_today()
    
    if request.method == "POST":
        # Get the new lease terms from the form
        new_rent_amount = request.POST.get("rentAmount")
        new_vat_amount = request.POST.get("vatAmount")
        new_signage_fees = request.POST.get("signageFees")
        new_parking_fees = request.POST.get("parkingFees")
        new_contract_length = request.POST.get("contractLength")
        new_contract_start = request.POST.get("contractStart")
        lease_attachment = request.FILES.get("leaseAttachment")
        admin_account = get_logged_in_account(request)
        
        # Validate inputs
        if not new_contract_start or not new_contract_length:
            messages.error(request, "Please provide both contract start date and contract length.")
            return render(request, "billingApp/renew_lease.html", {
                "lease": lease,
                "tenant": tenant,
                "building": building,
                "unit": unit,
            })
        
        # Validate contract start date is not older than allowed based on contract length
        from datetime import datetime, timedelta
        try:
            contract_start_date = datetime.strptime(new_contract_start, "%Y-%m-%d").date()
            contract_length = int(new_contract_length)
            today = datetime.now().date()
            
            # Calculate the oldest allowed start date based on contract length
            if contract_length == 6:
                oldest_allowed_date = today - timedelta(days=6*30)
            elif contract_length == 12:
                oldest_allowed_date = today - timedelta(days=12*30)
            else:
                oldest_allowed_date = today - timedelta(days=contract_length*30)
            
            if contract_start_date < oldest_allowed_date:
                messages.error(request, f"For a {contract_length}-month contract, the start date cannot be earlier than {oldest_allowed_date.strftime('%B %d, %Y')}.")
                return render(request, "billingApp/renew_lease.html", {
                    "lease": lease,
                    "tenant": tenant,
                    "building": building,
                    "unit": unit,
                })
        except ValueError:
            messages.error(request, "Invalid date format. Please use YYYY-MM-DD.")
            return render(request, "billingApp/renew_lease.html", {
                "lease": lease,
                "tenant": tenant,
                "building": building,
                "unit": unit,
            })
        
        try:
            # Convert values to appropriate types
            new_rent_amount = float(new_rent_amount) if new_rent_amount else 0.0
            new_vat_amount = float(new_vat_amount) if new_vat_amount else float(new_rent_amount * 0.12)
            new_signage_fees = float(new_signage_fees) if new_signage_fees else None
            new_parking_fees = float(new_parking_fees) if new_parking_fees else None
            new_contract_length = int(new_contract_length)
            
            # Calculate contract end date
            from datetime import datetime, timedelta
            contract_start_date = datetime.strptime(new_contract_start, "%Y-%m-%d").date()
            contract_end_date = contract_start_date + timedelta(days=new_contract_length * 30)

            if lease_attachment:
                allowed_types = ["application/pdf", "image/jpeg", "image/png", "image/webp"]

                if lease_attachment.content_type not in allowed_types:
                    messages.error(request, "Invalid file type. Please upload a PDF or image file.")
                    return render(request, "billingApp/renew_lease.html", {
                        "lease": lease,
                        "tenant": tenant,
                        "building": building,
                        "unit": unit,
                        "date_today": date_today,
                    })

            # Archive the old lease (mark as pastLease=True)
            lease.pastLease = True
            lease.modified_by = admin_account
            lease.save()

            # Create the new lease with updated terms
            new_lease = Lease.objects.create(
                buildingName=building,
                tenantName=tenant,
                unitID=unit,
                rentAmount=new_rent_amount,
                vatAmount=new_vat_amount,
                signageFees=new_signage_fees,
                parkingFees=new_parking_fees,
                contractLength=new_contract_length,
                contractStart=contract_start_date,
                contractEnd=contract_end_date,
                pastLease=False,
                modified_by=admin_account,
                leaseAttachment=lease_attachment,
            )
            
            messages.success(request, "Lease renewed successfully. Previous lease has been archived.")
            return redirect("tenant_details", pk=tenant.pk)
            
        except Exception as e:
            messages.error(request, f"Error renewing lease: {str(e)}")
            return render(request, "billingApp/renew_lease.html", {
                "lease": lease,
                "tenant": tenant,
                "building": building,
                "unit": unit,
            })
    
    
    # Display the form pre-populated with current lease information
    return render(request, "billingApp/renew_lease.html", {
        "lease": lease,
        "tenant": tenant,
        "building": building,
        "unit": unit,
        "date_today": date_today,
    })

def edit_tenant(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    date_today = get_date_today()

    if request.method == "POST":
        company_name = request.POST.get("company_name")
        contact_person = request.POST.get("contact_person")
        email = request.POST.get("email")
        phone_number = request.POST.get("phone_number")

        try:
            company_name = (company_name or "").strip()
            contact_person = (contact_person or "").strip()
            email = (email or "").strip()
            phone_number = (phone_number or "").strip()

            # Check if ALL fields are empty
            if not any([company_name, contact_person, email, phone_number]):
                messages.error(request, "Please enter at least one field to update.")
                return render(request, "billingApp/edit_tenant.html", {
                    "tenant": tenant,
                    "date_today": date_today
                })

            # Duplicate checks ONLY if field is being updated
            if company_name:
                duplicate_tenant = Tenant.objects.exclude(pk=tenant.pk).filter(
                    companyName__iexact=company_name
                ).exists()
                if duplicate_tenant:
                    messages.error(request, "Another tenant already has this company name.")
                    return render(request, "billingApp/edit_tenant.html", {
                        "tenant": tenant,
                        "date_today": date_today
                    })

            if contact_person:
                duplicate_contact = Tenant.objects.exclude(pk=tenant.pk).filter(
                    contactPerson__iexact=contact_person
                ).exists()
                if duplicate_contact:
                    messages.error(request, "Another tenant already has this contact person name.")
                    return render(request, "billingApp/edit_tenant.html", {
                        "tenant": tenant,
                        "date_today": date_today
                    })

            # Update ONLY fields that were filled
            if company_name:
                tenant.companyName = company_name

            if contact_person:
                tenant.contactPerson = contact_person

            if email:
                tenant.email = email

            if phone_number:
                tenant.phoneNumber = phone_number

            admin_account = get_logged_in_account(request)
            tenant.modified_by = admin_account
            tenant.save()
            
            messages.success(request, "Tenant updated successfully.")
            return redirect("tenant_details", pk=tenant.pk)
        except Exception as e:
            messages.error(request, f"Error updating tenant: {str(e)}")
            # Refresh from database to avoid stale data
            tenant = get_object_or_404(Tenant, pk=pk)
            return render(request, "billingApp/edit_tenant.html", {"tenant": tenant, "date_today": date_today})

    return render(request, "billingApp/edit_tenant.html", {"tenant": tenant, "date_today": date_today})

def edit_lease(request, pk):
    lease = get_object_or_404(Lease, pk=pk)
    tenant = lease.tenantName
    building = lease.buildingName
    unit = lease.unitID
    date_today = get_date_today()
    
    building_objects = Building.objects.all().order_by("buildingName")
    active_leased_unit_ids = Lease.objects.filter(pastLease=False).exclude(pk=lease.pk).values_list("unitID_id", flat=True)
    units = (
        Units.objects.select_related("building")
        .exclude(pk__in=active_leased_unit_ids)
        .order_by("building__buildingName", "unitID")
    )
    units_payload = [
        {
            "id": unit.id,
            "unitID": unit.unitID,
            "building_id": unit.building_id,
        }
        for unit in units
    ]

    if request.method == "POST":
        building_id = request.POST.get("building_id")
        unit_id = request.POST.get("unitID")
        rent_amount = request.POST.get("rentAmount")
        vat_amount = request.POST.get("vatAmount")
        signage_fees = request.POST.get("signageFees")
        parking_fees = request.POST.get("parkingFees")
        contract_length = request.POST.get("contractLength")
        contract_start = request.POST.get("contractStart")

        # Validate contract start date is not older than allowed based on contract length
        if contract_start and contract_length:
            from datetime import datetime, timedelta
            try:
                start_date = datetime.strptime(contract_start, "%Y-%m-%d").date()
                length = int(contract_length)
                today = datetime.now().date()
                
                # Calculate the oldest allowed start date based on contract length
                if length == 6:
                    oldest_allowed_date = today - timedelta(days=6*30)
                elif length == 12:
                    oldest_allowed_date = today - timedelta(days=12*30)
                else:
                    oldest_allowed_date = today - timedelta(days=length*30)
                
                if start_date < oldest_allowed_date:
                    messages.error(request, f"For a {length}-month contract, the start date cannot be earlier than {oldest_allowed_date.strftime('%B %d, %Y')}.")
                    return render(request, "billingApp/edit_lease.html", {
                        "lease": lease,
                        "tenant": tenant,
                        "building": building,
                        "units_payload": units_payload,
                        "building_objects": building_objects,
                    })
            except ValueError:
                messages.error(request, "Invalid date format. Please use YYYY-MM-DD.")
                return render(request, "billingApp/edit_lease.html", {
                    "lease": lease,
                    "tenant": tenant,
                    "building": building,
                    "units_payload": units_payload,
                    "building_objects": building_objects,
                })

        if building_id:
            selected_building = Building.objects.filter(pk=building_id).first()
            if selected_building:
                lease.buildingName = selected_building
        
        if unit_id:
            selected_unit = Units.objects.filter(pk=unit_id).first()
            if selected_unit:
                lease.unitID = selected_unit

        try:
            # Update all lease fields
            if rent_amount:
                lease.rentAmount = Decimal(rent_amount)
            if vat_amount:
                lease.vatAmount = Decimal(vat_amount)
            if signage_fees:
                lease.signageFees = Decimal(signage_fees) if signage_fees else None
            if parking_fees:
                lease.parkingFees = Decimal(parking_fees) if parking_fees else None
            if contract_length:
                lease.contractLength = int(contract_length)
            if contract_start:
                from datetime import datetime
                lease.contractStart = datetime.strptime(contract_start, "%Y-%m-%d").date()
            
            # Recalculate contract end date based on new contract start and length
            if contract_start and contract_length:
                from datetime import timedelta
                lease.contractEnd = lease.contractStart + timedelta(days=lease.contractLength * 30)

            admin_account = get_logged_in_account(request)
            lease.modified_by = admin_account
            lease.save()
            
            messages.success(request, "Lease updated successfully.")
            return redirect("tenant_details", pk=tenant.pk)
        except Exception as e:
            messages.error(request, f"Error updating lease: {str(e)}")
            # Refresh from database to avoid stale data
            lease = get_object_or_404(Lease, pk=pk)
            return render(request, "billingApp/edit_lease.html", {
                "lease": lease,
                "tenant": lease.tenantName,
                "building": lease.buildingName,
                "unit": lease.unitID,
                "buildings": building_objects,
                "units_payload": units_payload,
                "date_today": date_today,
            })

    return render(request, "billingApp/edit_lease.html", {
        "lease": lease,
        "tenant": tenant,
        "building": building,
        "unit": unit,
        "buildings": building_objects,
        "units_payload": units_payload,
        "date_today": date_today,
    })

def edit_payment(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    tenant = payment.tenantID
    bill = payment.billingID
    date_today = get_date_today()
    
    if request.method == "POST":
        amount_paid = request.POST.get("amountPaid")
        date_paid = request.POST.get("datePaid")
        reference_number = request.POST.get("referenceNumber")
        payment_method = request.POST.get("paymentMethod")
        sub_account_name = request.POST.get("subAccountName")
        proof_of_payment = request.FILES.get("proofOfPayment")

        if proof_of_payment:
            # 5MB limit
            max_size = 5 * 1024 * 1024  # 5MB in bytes

            if proof_of_payment.size > max_size:
                messages.error(request, "File too large. Maximum size is 5MB.")
                return redirect("edit_payment", pk=tenant.pk)

            # Allowed file types
            allowed_types = [
                "application/pdf",
                "image/jpeg",
                "image/png",
                "image/webp"
            ]

            if proof_of_payment.content_type not in allowed_types:
                messages.error(request, "Invalid file type. Only PDF and image files are allowed.")
                return redirect("add_payment", pk=tenant.pk)
        
        # Validate required fields
        if not amount_paid or not date_paid or not reference_number or not payment_method:
            messages.error(request, "Please fill in all required fields.")
            # Refresh from database to avoid stale data
            payment = get_object_or_404(Payment, pk=pk)
            return render(request, "billingApp/edit_payment.html", {
                "payment": payment,
                "tenant": payment.tenantID,
                "bill": payment.billingID,
                "date_today": date_today,
            })
        
        try:
            # Update all payment fields
            from datetime import datetime
            
            payment.amountPaid = Decimal(amount_paid)
            payment.datePaid = datetime.strptime(date_paid, "%Y-%m-%d").date()
            payment.referenceNumber = reference_number
            payment.paymentMethod = payment_method
            payment.subAccountName = sub_account_name if sub_account_name else None
            
            # Update proof of payment if a new file is provided
            if proof_of_payment:
                payment.proofOfPayment = proof_of_payment
            
            admin_account = get_logged_in_account(request)
            payment.modified_by = admin_account
            with transaction.atomic():
                payment.save()
                settle_bill_and_push_difference_to_next_bill(payment.billingID)

            messages.success(request, "Payment updated successfully.")
            return redirect("payment_details", pk=payment.pk)
            
        except Exception as e:
            messages.error(request, f"Error updating payment: {str(e)}")
            # Refresh from database to avoid stale data
            payment = get_object_or_404(Payment, pk=pk)
            return render(request, "billingApp/edit_payment.html", {
                "payment": payment,
                "tenant": payment.tenantID,
                "bill": payment.billingID,
                "date_today": date_today,
            })

    return render(request, "billingApp/edit_payment.html", {
        "payment": payment,
        "tenant": tenant,
        "bill": bill,
        "date_today": date_today
    })

@login_required
def add_penalty(request, pk):
    bill = get_object_or_404(BillingRecord, pk=pk)
    tenant = bill.tenant
    date_today = get_date_today()

    if request.method == "POST":
        penalty_raw = request.POST.get("penaltyAmount")

        try:
            penalty_amount = Decimal(str(penalty_raw)).quantize(Decimal("0.01"))
        except:
            messages.error(request, "Invalid penalty amount.")
            return redirect("add_penalty", pk=bill.pk)

        if penalty_amount <= Decimal("0.00"):
            messages.error(request, "Penalty must be greater than zero.")
            return redirect("add_penalty", pk=bill.pk)

        bill.penaltyFee = (bill.penaltyFee or Decimal("0.00")) + penalty_amount
        bill.modified_by = get_logged_in_account(request)
        bill.save(update_fields=["penaltyFee", "modified_by", "modified_at"])

        recalculate_bill_balance(bill)

        messages.success(request, "Penalty added successfully.")
        return redirect("view_bills", pk=tenant.pk)

    return render(request, "billingApp/add_penalty.html", {
        "bill": bill,
        "tenant": tenant,
        "date_today": date_today,
    })
