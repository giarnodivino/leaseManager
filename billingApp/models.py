from django.db import models
from datetime import timedelta, date
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
from django.db.models import Q

# Create your models here.
class Building(models.Model):
    buildingName = models.CharField(max_length=100, unique=True)
    signageCapacity = models.IntegerField(default=0)
    parkingCapacity = models.IntegerField(default=0)
    buildingAddress = models.CharField(max_length=200)
    modified_by = models.ForeignKey("Account", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    modified_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    objects = models.Manager

    def __str__(self):
        return str(self.buildingName)
    
class Units(models.Model):
    building = models.ForeignKey(Building, on_delete=models.CASCADE)
    unitID = models.IntegerField()
    modified_by = models.ForeignKey("Account", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    modified_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta:
        unique_together = ('building', 'unitID')

    def __str__(self):
        return f"{self.building.buildingName} - Unit {self.unitID}"

class Tenant(models.Model):
    companyName = models.CharField(max_length=100, null=True, blank=True)
    contactPerson = models.CharField(max_length=100)
    phoneNumber = models.CharField(max_length=12)
    email = models.CharField(max_length=50)
    modified_by = models.ForeignKey("Account", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    modified_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    objects = models.Manager
    
    def __str__(self):
        return str(self.pk) + ": " + self.contactPerson
    
class Lease(models.Model):
    buildingName = models.ForeignKey(Building, on_delete=models.CASCADE)
    tenantName = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    modified_by = models.ForeignKey("Account", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    modified_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    unitID = models.ForeignKey(Units, on_delete=models.CASCADE)
    rentAmount = models.DecimalField(max_digits=12, decimal_places=2)
    vatAmount = models.DecimalField(max_digits=12, decimal_places=2)
    signageFees = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    parkingFees = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    CONTRACT_CHOICES = [(6, "6 months"), (12, "12 months")]
    contractLength = models.PositiveSmallIntegerField(choices=CONTRACT_CHOICES)
    contractStart = models.DateField()
    contractEnd = models.DateField(null=True, blank=True)

    pastLease = models.BooleanField(default=False)  

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenantName"],
                condition=Q(pastLease=False),
                name="unique_active_lease_per_tenant",
            ),
        ]
    
class BillingRecord(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    lease = models.ForeignKey(Lease, on_delete=models.CASCADE)
    modified_by = models.ForeignKey("Account", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    modified_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    dateIssued = models.DateField()
    dateDue = models.DateField(null=True, blank=True)

    RENT = 'RENT'
    ELECTRICITY = 'ELECTRICITY'
    WATER = 'WATER'

    BILLING_CHOICES = [
        (RENT, 'Rent'),
        (ELECTRICITY, 'Electricity'),
        (WATER, 'Water'),
    ]

    billingFor = models.CharField(max_length=20, choices=BILLING_CHOICES)

    amountDue = models.DecimalField(max_digits=12, decimal_places=2)

    penaltyFee = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    STATUS_UNPAID = "UNPAID"
    STATUS_PARTIAL = "PARTIAL"
    STATUS_PAID = "PAID"

    STATUS_CHOICES = [
        (STATUS_UNPAID, "Unpaid"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_PAID, "Paid"),
    ]

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_UNPAID)

    balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    

    def save(self, *args, **kwargs):

        if self.lease_id:
            self.tenant = self.lease.tenantName

        if self.dateIssued and isinstance(self.dateIssued, str):
            self.dateIssued = date.fromisoformat(self.dateIssued)

        if self.dateIssued and not self.dateDue:
            self.dateDue = self.dateIssued + timedelta(days=30)

        if self.pk is None and self.balance is None:
            self.balance = self.amountDue or Decimal("0.00")

        super().save(*args, **kwargs)

    def billing_number(self):
        if self.pk:
            return f"BL-{self.pk:06d}"

    def __str__(self):
        return f"{self.billing_number()}: Bill for {self.tenant}"

class Account(models.Model):
    firstName = models.CharField(max_length=30)
    lastName = models.CharField(max_length=30)
    username = models.CharField(max_length=20)
    password = models.CharField(max_length=20)
    objects = models.Manager()

    def getUsername(self):
        return self.username
    
    def getPassword(self):
        return self.password
    
    def __str__(self):
        return self.username