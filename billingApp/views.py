from django.shortcuts import render, redirect, get_object_or_404
from .models import Tenant, Building, Lease, BillingRecord, Account
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required

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
    return render(request, 'billingApp/home_page.html')

@login_required
def buildings_main(request):
    building_objects = Building.objects.all()
    return render(request, 'billingApp/buildings_main.html', {'buildings':building_objects})

@login_required
def tenants_main(request):
    tenant_objects = Tenant.objects.all().order_by('companyName')

    for t in tenant_objects:
        if not (t.companyName or "").strip():
            t.companyName_display = t.contactPerson

        else:
            t.companyName_display = t.companyName

    return render(request, 'billingApp/tenants_main.html', {'tenants': tenant_objects})

@login_required
def add_building(request):
    if(request.method=="POST"):
        buildingName = request.POST.get('buildingName')
        buildingAddress = request.POST.get('buildingAddress')
        roomCapacity = request.POST.get('roomCapacity')
        signageCapacity = request.POST.get('signageCapacity')
        parkingCapacity = request.POST.get('parkingCapacity')
        if not Building.objects.filter(buildingName=buildingName).exists():
            Building.objects.create(buildingName=buildingName, buildingAddress=buildingAddress, 
                                    roomCapacity=roomCapacity, signageCapacity=signageCapacity, 
                                    parkingCapacity=parkingCapacity)
        else:
            return redirect('add_building')
        return redirect('buildings_main')
    else:
        return render(request, 'billingApp/add_building.html')

@login_required
def building_details(request, pk):
    building = get_object_or_404(Building, pk=pk)

    return render(request, 'billingApp/building_details.html', {'b':building})

@login_required
def delete_building(request, pk):
    b = get_object_or_404(Building, pk=pk)
    Building.objects.filter(pk=pk).delete()
    return redirect('buildings_main')

@login_required
def add_tenant(request):
    if(request.method=="POST"):
        companyName = request.POST.get('companyName')
        contactPerson = request.POST.get('contactPerson')
        phoneNumber = request.POST.get('phoneNumber')
        email = request.POST.get('email')
        if not Tenant.objects.filter(companyName=companyName).exists():
            Tenant.objects.create(companyName=companyName, contactPerson=contactPerson, 
                                    phoneNumber=phoneNumber, email=email)
        else:
            return redirect('add_tenant')
        return redirect('tenants_main')
    return render(request, 'billingApp/add_tenant.html', {})

@login_required
def tenant_details(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    leases = Lease.objects.filter(tenantName=tenant)
    return render(request, 'billingApp/tenant_details.html', {'t':tenant, 'lease':leases})

@login_required
def delete_tenant(request, pk):
    t = get_object_or_404(Tenant, pk=pk)
    Tenant.objects.filter(pk=pk).delete()
    return redirect('tenants_main')

@login_required
def add_lease(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    tenant_objects = Tenant.objects.all()
    building_objects = Building.objects.all()

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

        Lease.objects.create(
            buildingName_id=building_id,
            tenantName=tenant,
            unitID=unitID,
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

    return render(request, "billingApp/add_lease.html", {"t": tenant, "tenants": tenant_objects, "buildings": building_objects})

@login_required
def delete_lease(request, pk):
    lease = get_object_or_404(Lease, pk=pk)

    tenant_pk = lease.tenantName_id  

    lease.delete()

    return redirect('tenant_details', pk=tenant_pk)

@login_required
def billing_records_main(request):
    tenants = Tenant.objects.all().order_by('companyName')
    leases = Lease.objects.all()

    for t in tenants:
        if not (t.companyName or "").strip():
            t.companyName_display = t.contactPerson

        else:
            t.companyName_display = t.companyName

    return render(
        request,
        'billingApp/billing_records_main.html',
        {'tenants': tenants, 'leases': leases}
    )

@login_required
def view_bills(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    bills = BillingRecord.objects.filter(tenant=tenant).order_by("-id")

    if not (tenant.companyName or "").strip():
        tenant.companyName_display = tenant.contactPerson
    else:
        tenant.companyName_display = tenant.companyName
    return render(request, 'billingApp/view_bills.html', {"tenant": tenant, "bills": bills})

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
            billing_for = request.POST.get("billingFor")
            date_issued = request.POST.get("dateIssued")
            amountdue = request.POST.get("payable")

            if billing_for and date_issued:
                if (billing_for or "").upper() == "RENT":
                    amountdue = lease.rentAmount
                    BillingRecord.objects.create(
                        tenant=tenant,
                        lease=lease,
                        dateIssued=date_issued,
                        billingFor=billing_for,
                        amountDue=amountdue,
                    )
                else:
                    BillingRecord.objects.create(
                        tenant=tenant,
                        lease=lease,
                        dateIssued=date_issued,
                        billingFor=billing_for,
                        amountDue=amountdue,
                    )
            else:
                return redirect("add_bill", pk=tenant.pk)
            
            return redirect("view_bills", pk=tenant.pk)
    else:
        messages.error(request, "This tenant does not have an active lease.")
        return redirect("view_bills", pk=tenant.pk)

    return render(request, "billingApp/add_bill.html", {"tenant": tenant, "lease": lease})