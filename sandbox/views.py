from django.shortcuts import render
from django.http import HttpResponse

# Create your views here.
def test_page(request):
    return render(request, "sandbox/test.html")
    # return HttpResponse("Hello")