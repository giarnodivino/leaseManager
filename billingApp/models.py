from django.db import models
from datetime import timedelta, date

# Create your models here.
class Building(models.Model):
    buildingName = models.CharField(max_length=100, unique=True)
    roomCapacity = models.IntegerField()
    signageCapacity = models.IntegerField(default=0)
    parkingCapacity = models.IntegerField(default=0)
    buildingAddress = models.CharField(max_length=200)
    objects = models.Manager

    def __str__(self):
        return str(self.buildingName)

class Tenant(models.Model):
    companyName = models.CharField(max_length=100)
    contactPerson = models.CharField(max_length=100)
    phoneNumber = models.CharField(max_length=12)
    email = models.CharField(max_length=50)
    objects = models.Manager
    
    def __str__(self):
        return str(self.pk) + ": " + self.contactPerson
    
class Lease(models.Model):
    buildingName = models.ForeignKey(Building, on_delete=models.CASCADE)
    tenantName = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    unitID = models.IntegerField()
    rentAmount = models.FloatField()
    vatAmount = models.FloatField(null=True, blank=True)
    signageFees = models.FloatField(null=True, blank=True)
    parkingFees = models.FloatField(null=True, blank=True)
    
    CONTRACT_CHOICES = [
        (6, "6 months"),
        (12, "12 months"),
    ]

    contractLength = models.PositiveSmallIntegerField(choices=CONTRACT_CHOICES)
    contractStart = models.DateField()
    contractEnd = models.DateField()

    pastLease = models.BooleanField(default=True, blank=True, null=True)

    def save(self, *args, **kwargs):
        if self.contractStart and self.contractLength:
            if isinstance(self.contractStart, str):
                self.contractStart = date.fromisoformat(self.contractStart)

            length = int(self.contractLength)

            if length == 12:
                self.contractEnd = self.contractStart + timedelta(days=365)
            elif length == 6:
                self.contractEnd = self.contractStart + timedelta(days=182)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tenantName} - {self.buildingName} (Unit {self.unitID})"