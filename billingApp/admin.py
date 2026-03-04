from django.contrib import admin
from .models import Account, Tenant, Building, Lease, BillingRecord, Units

# Register your models here.
admin.site.register(Tenant)
admin.site.register(Building)
admin.site.register(Lease)
admin.site.register(BillingRecord)
admin.site.register(Account)
admin.site.register(Units)