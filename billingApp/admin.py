from django.contrib import admin
from .models import Tenant, Building, Lease, BillingRecord

# Register your models here.
admin.site.register(Tenant)
admin.site.register(Building)
admin.site.register(Lease)
admin.site.register(BillingRecord)