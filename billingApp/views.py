from django.shortcuts import render, redirect, get_object_or_404
from .models import Tenant, Building, Lease, BillingRecord
from django.contrib import messages

# Create your views here.
def home_page(request):
    return render(request, 'billingApp/home_page.html')

def buildings_main(request):
    building_objects = Building.objects.all()
    return render(request, 'billingApp/buildings_main.html', {'buildings':building_objects})

def tenants_main(request):
    tenant_objects = Tenant.objects.all()
    return render(request, 'billingApp/tenants_main.html', {'tenants':tenant_objects})

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

def building_details(request, pk):
    building = get_object_or_404(Building, pk=pk)

    return render(request, 'billingApp/building_details.html', {'b':building})

def delete_building(request, pk):
    b = get_object_or_404(Building, pk=pk)
    Building.objects.filter(pk=pk).delete()
    return redirect('buildings_main')

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

def tenant_details(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    leases = Lease.objects.filter(tenantName=tenant)
    return render(request, 'billingApp/tenant_details.html', {'t':tenant, 'lease':leases})

def delete_tenant(request, pk):
    t = get_object_or_404(Tenant, pk=pk)
    Tenant.objects.filter(pk=pk).delete()
    return redirect('tenants_main')

def add_lease(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    tenant_objects = Tenant.objects.all()
    building_objects = Building.objects.all()

    # Block if tenant already has an active/current lease
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

        # your conversions...
        rentAmount = float(rentAmount) if rentAmount else 0.0
        vatAmount = float(rentAmount * 0.12)
        signageFees = float(signageFees) if signageFees else None
        parkingFees = float(parkingFees) if parkingFees else None
        contractLength = int(contractLength) if contractLength else None

        # Double-check again before create (race-condition safe-ish)
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
            pastLease=False,
            signageFees=signageFees,
            parkingFees=parkingFees,
        )
        return redirect("tenant_details", pk=tenant.pk)

    return render(request, "billingApp/add_lease.html", {"t": tenant, "tenants": tenant_objects, "buildings": building_objects})

def delete_lease(request, pk):
    lease = get_object_or_404(Lease, pk=pk)

    tenant_pk = lease.tenantName_id  

    lease.delete()

    return redirect('tenant_details', pk=tenant_pk)


def billing_records_main(request):
    tenants = Tenant.objects.all()
    leases = Lease.objects.all()
    return render(
        request,
        'billingApp/billing_records_main.html',
        {'tenants': tenants, 'leases': leases}
    )


def view_bills(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    bills = BillingRecord.objects.filter(tenant=tenant).order_by("-id")
    return render(request, 'billingApp/view_bills.html', {"tenant": tenant, "bills": bills})

def add_bill(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)

    lease = (
        Lease.objects
        .filter(tenantName=tenant)
        .order_by("-contractStart")
        .first()
    )

    if request.method == "POST":
        billing_for = request.POST.get("billingFor")
        date_issued = request.POST.get("dateIssued")
        amountdue = request.POST.get("payable")

        if billing_for and date_issued:
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

    return render(request, "billingApp/add_bill.html", {"tenant": tenant, "lease": lease})