from django.urls import path
from . import views

urlpatterns = [
    path('', views.chatbot, name='chatbot'),
    path('stream/', views.chatbot_stream, name='chatbot_stream'),
    path('vits-speech/', views.vits_speech, name='vits_speech'),
    path('login', views.login, name='login'),
    path('register', views.register, name='register'),
    path('logout', views.logout, name='logout'),
]