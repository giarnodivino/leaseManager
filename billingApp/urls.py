from django.urls import path
from . import views

urlpatterns = [
    path('', views.home_page, name='home_page'),
    path('buildings_main/', views.buildings_main, name='buildings_main'),
    path('tenants_main/', views.tenants_main, name='tenants_main'),
    path('add_building/', views.add_building, name='add_building'),
    path('building_details/<int:pk>/', views.building_details, name='building_details'),
    path('delete_building/<int:pk>/', views.delete_building, name='delete_building'),
    path('add_tenant/', views.add_tenant, name='add_tenant'),
    path('tenant_details/<int:pk>/', views.tenant_details, name='tenant_details'),
    path('delete_tenant/<int:pk>/', views.delete_tenant, name='delete_tenant'),
    path('add_lease/<int:pk>/', views.add_lease, name='add_lease')
]
