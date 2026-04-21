from django.shortcuts import render, redirect, get_object_or_404
from .models import Tenant, Building, Lease, BillingRecord, Account, Units, Payment
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from decimal import Decimal, ROUND_HALF_UP


def get_available_units(building_id):
    leased_units = Lease.objects.filter(buildingName_id=building_id, pastLease=False).values_list("unitID_id", flat=True)
    available_units = Units.objects.filter(building_id=building_id).exclude(pk__in=leased_units)
    return available_units

def calculate_total_outstanding():
    total_outstanding = Decimal("0.00")
    billing_records = BillingRecord.objects.filter(
        status__in=[BillingRecord.STATUS_UNPAID, BillingRecord.STATUS_PARTIAL]
    )
    for record in billing_records:
        total_outstanding += record.balance or Decimal("0.00")
    return f"{total_outstanding:,.2f}"

# total revenue should include all paid and unpaid bills
def calculate_total_revenue():
    total_revenue = Decimal("0.00")
    billing_records = BillingRecord.objects.all()
    for record in billing_records:
        total_revenue += record.amountDue or Decimal("0.00")
    return f"{total_revenue:,.2f}"

def calculate_total_paid():
    total_paid = Decimal("0.00")
    payments = Payment.objects.all()
    for payment in payments:
        total_paid += payment.amountPaid or Decimal("0.00")
    return f"{total_paid:,.2f}"


def get_date_today():
    from datetime import datetime
    return datetime.now().strftime("%B %d, %Y")

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
    return render(request, 'billingApp/home_page.html', {'total_outstanding': total_outstanding_balance, 'total_revenue': total_revenue, 'total_paid': total_paid})

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

        Tenant.objects.create(companyName=companyName, contactPerson=contactPerson, 
                                phoneNumber=phoneNumber, email=email,
                                modified_by=admin_account)
        return redirect('tenants_main')
    return render(request, 'billingApp/add_tenant.html', {})

@login_required
def tenant_details(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    leases = Lease.objects.filter(tenantName=tenant)

    if not (tenant.companyName or "").strip():
        tenant.companyName_display = tenant.contactPerson
    else:
        tenant.companyName_display = tenant.companyName
        
    date_today = get_date_today()
    return render(request, 'billingApp/tenant_details.html', {'t':tenant, 'lease':leases, 'date_today': date_today})

@login_required
def delete_tenant(request, pk):
    t = get_object_or_404(Tenant, pk=pk)
    Tenant.objects.filter(pk=pk).delete()
    return redirect('tenants_main')


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

    if Lease.objects.filter(tenantName=tenant, pastLease=False).exists():
        messages.error(request, "This tenant already has an active lease.")
        return redirect("tenant_details", pk=tenant.pk)

    if request.method == "POST":
        building_id = request.POST.get("building_id")
        unitID = request.POST.get("unitID")
        rentAmount = request.POST.get("rentAmount")
        contractLength = request.POST.get("contractLength")
        contractStart = request.POST.get("contractStart")
        signageFees = request.POST.get("signageFees")
        parkingFees = request.POST.get("parkingFees")
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

        if Lease.objects.filter(tenantName=tenant, pastLease=False).exists():
            messages.error(request, "This tenant already has an active lease.")
            return redirect("tenant_details", pk=tenant.pk)

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

    lease.delete()

    return redirect('tenant_details', pk=tenant_pk)

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
def view_bills(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    bills = BillingRecord.objects.filter(tenant=tenant).order_by("-id")
    payments = Payment.objects.filter(tenantID=tenant)


    total_unpaid_balance = Decimal("0.00")

    if not (tenant.companyName or "").strip():
        tenant.companyName_display = tenant.contactPerson
    else:
        tenant.companyName_display = tenant.companyName

    for b in bills:
        # Balance is now maintained in the database, no need to recalculate
        b.balance = b.balance or Decimal("0.00")
        if b.status == BillingRecord.STATUS_OVERPAID:
            # Show overpaid amount from carryover_balance
            b.balance_display = f"{tenant.carryover_balance or Decimal('0.00'):,.2f}"
        elif b.balance > Decimal("0.00"):
            total_unpaid_balance += b.balance
            b.balance_display = f"{b.balance:,.2f}"
        else:
            b.balance_display = "0.00"

    carryover_due = Decimal("0.00")
    if tenant.carryover_balance and tenant.carryover_balance < Decimal("0.00"):
        carryover_due = abs(tenant.carryover_balance)
        total_unpaid_balance += carryover_due

    has_active_lease = Lease.objects.filter(tenantName=tenant, pastLease=False).exists()

    date_today = get_date_today()



    return render(
        request,
        'billingApp/view_bills.html',
        {
            "tenant": tenant,
            "bills": bills,
            "payments": payments,
            "has_active_lease": has_active_lease,
            "date_today": date_today,
            "total_unpaid_balance": f"{total_unpaid_balance:,.2f}",
            "carryover_due_display": f"{carryover_due:,.2f}",
        },
    )

@login_required
def add_bill(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)

    lease = (
        Lease.objects
        .filter(tenantName=tenant)
        .order_by("-contractStart")
        .first()
    )

    if lease:
        if request.method == "POST":
            billing_for = (request.POST.get("billingFor") or "").upper()
            date_issued = request.POST.get("dateIssued")
            amountdue_raw = request.POST.get("payable")
            admin_account = get_logged_in_account(request)

            if billing_for and date_issued:
                if billing_for == "RENT":
                    base_amount = (
                        (lease.rentAmount or Decimal("0.00"))
                        + (lease.parkingFees or Decimal("0.00"))
                        + (lease.signageFees or Decimal("0.00"))
                    )
                else:
                    amountdue_raw = (amountdue_raw or "0").replace(",", "")
                    base_amount = Decimal(amountdue_raw)

                carryover = tenant.carryover_balance or Decimal("0.00")
                adjusted_amount = base_amount - carryover
                amountdue = adjusted_amount.quantize(Decimal("0.01"))

                if amountdue <= Decimal("0.00"):
                    tenant.carryover_balance = -adjusted_amount
                    amountdue = Decimal("0.00")
                else:
                    tenant.carryover_balance = Decimal("0.00")

                tenant.save(update_fields=["carryover_balance"])

                bill = BillingRecord.objects.create(
                    tenant=tenant,
                    lease=lease,
                    modified_by=admin_account,
                    dateIssued=date_issued,
                    billingFor=billing_for,
                    amountDue=amountdue,
                )

                if amountdue == Decimal("0.00"):
                    bill.status = BillingRecord.STATUS_PAID
                    bill.balance = Decimal("0.00")
                    bill.save(update_fields=["status", "balance"])
            else:
                return redirect("add_bill", pk=tenant.pk)
            
            return redirect("view_bills", pk=tenant.pk)
    else:
        messages.error(request, "This tenant does not have an active lease.")
        return redirect("view_bills", pk=tenant.pk)

    return render(request, "billingApp/add_bill.html", {"tenant": tenant, "lease": lease})

@login_required
def add_units(request, pk):
    building_details = get_object_or_404(Building, pk=pk)
    if request.method == "POST":
        building_id = building_details.pk
        unit_number = request.POST.get("unit_number")
        
        if building_id and unit_number:
            existing_unit = Units.objects.filter(building_id=building_id, unitID=unit_number).first()
            if existing_unit:
                messages.error(request, "This unit already exists.")
                return redirect("add_unit", pk=building_id)
            Units.objects.create(building_id=building_id, unitID=unit_number)
            messages.add_message(request, messages.SUCCESS, "Unit added successfully.")
            return redirect("add_unit", pk=building_id)
        else:
            messages.error(request, "Please provide both building and unit number.")
            return redirect("add_unit", pk=building_id)

    buildings = Building.objects.all()

    date_today = get_date_today()
    return render(request, "billingApp/add_unit.html", {"buildings": buildings, "building": building_details, "date_today": date_today})


@login_required
def view_units(request, pk):
    building = get_object_or_404(Building, pk=pk)
    units = Units.objects.filter(building=building).order_by("unitID")
    date_today = get_date_today()
    return render(request, "billingApp/view_units.html", {"building": building, "units": units, "date_today": date_today})


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

def view_payments(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    payments = Payment.objects.filter(tenantID=tenant).order_by("-id")

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
    lease = (
        Lease.objects
        .filter(tenantName=tenant, pastLease=False)
        .select_related("buildingName", "unitID")
        .order_by("-contractStart", "-id")
        .first()
    )

    if not lease:
        messages.error(request, "This tenant does not have an active lease. Unable to generate SOA.")
        return redirect("view_bills", pk=tenant.pk)

    bills_qs = BillingRecord.objects.filter(
        tenant=tenant,
        status__in=[
            BillingRecord.STATUS_UNPAID,
            BillingRecord.STATUS_PARTIAL,
            BillingRecord.STATUS_UNDERPAID,
        ],
    ).order_by("dateIssued", "id")

    ids = request.GET.get("ids")
    if ids:
        id_list = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
        bills_qs = bills_qs.filter(id__in=id_list)

    lines = []
    grand_total = Decimal("0.00")

    for b in bills_qs:
        amount = (b.balance or Decimal("0.00")).quantize(Decimal("0.01"))

        bill_no = f"BL-{b.id:06d}"
        
        if b.billingFor == BillingRecord.RENT:
            # For rent bills, calculate components
            rent_amount = (b.lease.rentAmount or Decimal("0.00")).quantize(Decimal("0.01"))
            parking_fees = (b.lease.parkingFees or Decimal("0.00")).quantize(Decimal("0.01"))
            signage_fees = (b.lease.signageFees or Decimal("0.00")).quantize(Decimal("0.01"))
            
            # Derive VAT as 12% of rent amount
            vat_amount = (rent_amount * Decimal("0.12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            # Base rent = rent amount - derived VAT
            base_rent = (rent_amount - vat_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            
            # Calculate total original amount
            original_total = base_rent + vat_amount + parking_fees + signage_fees
            
            # Calculate scale factor for remaining balance
            if original_total > Decimal("0.00"):
                scale = amount / original_total
            else:
                scale = Decimal("1.00")
            
            # Scale each component
            scaled_base_rent = (base_rent * scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            scaled_vat = (vat_amount * scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            scaled_parking = (parking_fees * scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            scaled_signage = (signage_fees * scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            
            # Combine into one line
            total_amount = scaled_base_rent + scaled_parking + scaled_signage
            total_vat = scaled_vat
            total_line = total_amount + total_vat
            
            lines.append({
                "no": bill_no,
                "date": b.dateIssued,
                "particulars": "Rent",
                "amount": total_amount,
                "vat": total_vat,
                "total": total_line,
                "amount_display": f"{total_amount:,.2f}",
                "vat_display": f"{total_vat:,.2f}",
                "total_display": f"{total_line:,.2f}",
            })
            
            grand_total += amount
            
        else:
            # For non-rent bills
            total = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            grand_total += total

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

    if tenant.carryover_balance and tenant.carryover_balance != Decimal("0.00"):
        from datetime import date

        if tenant.carryover_balance > 0:
            carryover_total = -tenant.carryover_balance
            particulars = "Carryover Credit (Overpayment)"
        else:
            carryover_total = abs(tenant.carryover_balance)
            particulars = "Carryover Due (Underpayment)"

        lines.append({
            "no": "",
            "date": date.today(),
            "particulars": particulars,
            "amount": Decimal("0.00"),
            "vat": Decimal("0.00"),
            "total": carryover_total,
            "amount_display": "0.00",
            "vat_display": "0.00",
            "total_display": f"{carryover_total:,.2f}",
        })
        grand_total += carryover_total

    company_display = (tenant.companyName or "").strip() or tenant.contactPerson

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
    }
    return render(request, "billingApp/soa.html", context)


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
        status__in=[
            BillingRecord.STATUS_UNPAID,
            BillingRecord.STATUS_PARTIAL,
            BillingRecord.STATUS_UNDERPAID,
        ],
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
        if bill.status == BillingRecord.STATUS_UNDERPAID:
            bill.display_label = "Underpaid by"
            bill.display_amount = f"{abs(tenant.carryover_balance or Decimal('0.00')):,.2f}"
        elif bill.status == BillingRecord.STATUS_OVERPAID:
            bill.display_label = "Overpaid by"
            bill.display_amount = f"{tenant.carryover_balance or Decimal('0.00'):,.2f}"
        else:
            bill.display_label = "Due"
            bill.display_amount = bill.balance if bill.balance and bill.balance > Decimal("0.00") else bill.amountDue

    if request.method == "POST":
        billing_id = request.POST.get("bill_id")
        amount_paid_raw = request.POST.get("amountPaid")
        sub_account_name = request.POST.get("subAccountName")
        date_paid = request.POST.get("datePaid")
        payment_method = request.POST.get("paymentMethod")
        reference_number = (request.POST.get("referenceNumber") or "").strip()
        admin_account = get_logged_in_account(request)

        if Payment.objects.filter(referenceNumber=reference_number).exists():
            messages.error(request, "This reference number has already been used for another payment.")
            return redirect("add_payment", pk=tenant.pk)

        if not (billing_id and amount_paid_raw and date_paid and payment_method and reference_number):
            messages.error(request, "Please fill in all required fields.")
            return redirect("add_payment", pk=tenant.pk)

        try:
            amount_paid = Decimal(str(amount_paid_raw))
        except (TypeError, ValueError, ArithmeticError):
            messages.error(request, "Amount paid is invalid.")
            return redirect("add_payment", pk=tenant.pk)

        if amount_paid < Decimal("0"):
            messages.error(request, "Amount paid cannot be negative.")
            return redirect("add_payment", pk=tenant.pk)

        try:
            billing_id = int(billing_id)
        except (TypeError, ValueError):
            messages.error(request, "Selected bill is invalid.")
            return redirect("add_payment", pk=tenant.pk)

        bill_to_pay = bills.filter(pk=billing_id).first()
        if not bill_to_pay:
            messages.error(request, "Selected bill is invalid.")
            return redirect("add_payment", pk=tenant.pk)

        try:
            Payment.objects.create(
                tenantID=tenant,
                modified_by=admin_account,
                amountPaid=amount_paid,
                subAccountName=sub_account_name or None,
                datePaid=date_paid,
                billingID=bill_to_pay,
                referenceNumber=reference_number,
                paymentMethod=payment_method,
            )

            # Update the bill's balance after payment and carry over any overpayment/underpayment.
            if bill_to_pay.status == BillingRecord.STATUS_UNDERPAID:
                # For underpaid bills, payment is applied to the carryover balance
                underpaid_amount = abs(tenant.carryover_balance or Decimal("0.00"))
                tenant.carryover_balance = (tenant.carryover_balance or Decimal("0.00")) + amount_paid
                bill_to_pay.balance = Decimal("0.00")
                if tenant.carryover_balance > Decimal("0.00"):
                    # Overpayment: credit for next bill
                    bill_to_pay.status = BillingRecord.STATUS_OVERPAID
                elif tenant.carryover_balance == Decimal("0.00"):
                    # Exact payment: no carryover
                    bill_to_pay.status = BillingRecord.STATUS_PAID
                # else carryover_balance < 0: still UNDERPAID
                tenant.save(update_fields=["carryover_balance"])
            else:
                old_balance = bill_to_pay.balance if bill_to_pay.balance is not None else bill_to_pay.amountDue
                remaining_balance = old_balance - amount_paid

                if remaining_balance < Decimal("0.00"):
                    # Overpayment becomes tenant credit for next bill.
                    tenant.carryover_balance = (tenant.carryover_balance or Decimal("0.00")) + (-remaining_balance)
                    bill_to_pay.status = BillingRecord.STATUS_OVERPAID
                    bill_to_pay.balance = Decimal("0.00")
                    tenant.save(update_fields=["carryover_balance"])
                elif remaining_balance == Decimal("0.00"):
                    bill_to_pay.status = BillingRecord.STATUS_PAID
                    bill_to_pay.balance = Decimal("0.00")
                else:
                    # Underpayment is carried to the next bill.
                    bill_to_pay.status = BillingRecord.STATUS_UNDERPAID
                    bill_to_pay.balance = Decimal("0.00")
                    tenant.carryover_balance = (tenant.carryover_balance or Decimal("0.00")) - remaining_balance
                    tenant.save(update_fields=["carryover_balance"])

            bill_to_pay.save()

            return redirect("view_payments", pk=tenant.pk)
        except Exception:
            messages.error(request, "Unable to save payment. Please verify the form values.")
            return redirect("add_payment", pk=tenant.pk)
    
    return render(request, "billingApp/add_payment.html", {"tenant": tenant, "lease": lease, "bills": bills})

def delete_payment(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    tenant_pk = payment.tenantID_id
    bill = payment.billingID
    amount_paid = payment.amountPaid or Decimal("0.00")
    payment.delete()

    # Update the bill's balance after deleting payment
    bill.balance = (bill.balance or Decimal("0.00")) + amount_paid
    if bill.balance >= bill.amountDue:
        bill.status = BillingRecord.STATUS_UNPAID
        bill.balance = bill.amountDue
    elif bill.balance > Decimal("0.00"):
        bill.status = BillingRecord.STATUS_PARTIAL
    else:
        bill.status = BillingRecord.STATUS_PAID
        bill.balance = Decimal("0.00")
    bill.save()

    return redirect("view_payments", pk=tenant_pk)

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

    # Only allow editing if bill is not fully paid
    if bill.status == BillingRecord.STATUS_PAID:
        messages.error(request, "Cannot edit a fully paid bill.")
        return redirect("view_bills", pk=tenant.pk)
    
    if request.method == "POST":
        # Get form data from edit_bill.html and update bill accordingly
        date_issued = request.POST.get('date_issued')
        amount = request.POST.get('amount')
        due_date = request.POST.get('due_date')
        status = request.POST.get('status')
        particulars = request.POST.get('particulars')
        admin_account = get_logged_in_account(request)
        
        try:
            # Update bill fields
            if date_issued:
                bill.dateIssued = date_issued
            if amount:
                bill.amountDue = Decimal(amount)
            if due_date:
                bill.dateDue = due_date
            if status:
                bill.status = status
            if particulars:
                bill.billingFor = particulars
            
            bill.modified_by = admin_account
            bill.save()
            
            messages.success(request, "Bill updated successfully.")
            return redirect("view_bills", pk=tenant.pk)
        except Exception as e:
            messages.error(request, f"Error updating bill: {str(e)}")
            return render(request, "billingApp/edit_bill.html", {"tenant": tenant, "bill": bill})
    
    return render(request, "billingApp/edit_bill.html", {"tenant": tenant, "bill": bill})