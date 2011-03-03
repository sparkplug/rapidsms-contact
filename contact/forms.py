from django import forms
from rapidsms.models import Contact,Connection
from django.forms.widgets import Widget
from django.template.loader import get_template
from django.template.context import Context
from django.core.paginator import Paginator, Page
from django.contrib.auth.models import Group
from status160.models import  Team
from django.db.models import Q
from django.forms.widgets import HiddenInput
from rapidsms_httprouter.router import get_router, start_sending_mass_messages, stop_sending_mass_messages
from rapidsms.messages.outgoing import OutgoingMessage
from generic.forms import ActionForm, FilterForm
from ureport.models import MassText
from django.contrib.sites.models import Site
from django.shortcuts import get_object_or_404

from simple_locations.models import Area

class FilterGroupsForm(FilterForm):
    """ concrete implementation of filter form """
    # This may seem like a hack, but this allows time for the Contact model's
    # default manage to be replaced at run-time.  There are many applications
    # for that, such as filtering contacts by site_id (as is done in the
    # authsites app, see github.com/daveycrockett/authsites).
    # This does, however, also make the polling app independent of authsites.
    def __init__(self, data=None, **kwargs):
        if data:
            forms.Form.__init__(self, data, **kwargs)
        else:
            forms.Form.__init__(self, **kwargs)
        if hasattr(Contact, 'groups'):
            self.fields['groups'] = forms.ModelMultipleChoiceField(queryset=Group.objects.all(), required=True)    

    def filter(self,request, queryset):
        return queryset.filter(groups__in=self.cleaned_data['groups'])

class NewContactForm(forms.ModelForm):

    class Meta:
        model = Contact

class FreeSearchForm(FilterForm):
    """ concrete implementation of filter form """

    term = forms.CharField(max_length=100, required=True)

    def filter(self, request, queryset):
        term=self.cleaned_data['term']
        return queryset.filter(Q(name__icontains=term)
            | Q(reporting_location__name__icontains=term))

class DistictFilterForm(FilterForm):
    """ filter cvs districs on their districts """
    district=forms.ChoiceField(choices=(('','-----'),)+tuple([(int(d.pk),d.name) for d in Area.objects.filter(kind__slug='district') ])+((-1,'No District'),))
    def filter(self,request,queryset):
        district_pk=self.cleaned_data['district']
        if district_pk=='':
            return queryset
        elif int(district_pk)==-1:
            return queryset.filter(reporting_location=None)
        else:

            try:
                district=Area.objects.get(pk=district_pk)
            except Area.DoesNotExist:
                district=None
            if district:
                return queryset.filter(reporting_location__in=district.get_descendants(include_self=True))
            else:
                return queryset

class MassTextForm(ActionForm):

    text = forms.CharField(max_length=160, required=True)
    action_label = 'Send Message'

    def perform(self, request, results):
        connections = Connection.objects.filter(contact__in=results).distinct()
        router = get_router()
        text = self.cleaned_data['text']
        mass_text = MassText.objects.create(user=request.user, text=text)
        mass_text.sites.add(Site.objects.get_current())
        start_sending_mass_messages()
        for conn in connections:
            mass_text.contacts.add(conn.contact)
            outgoing = OutgoingMessage(conn, text)
            router.handle_outgoing(outgoing)
        stop_sending_mass_messages()
        return "Message successfully sent to %d numbers" % connections.count()


class AssignGroupForm(ActionForm):

    action_label = 'Assign to group(s)'

    # This may seem like a hack, but this allows time for the Contact model's
    # default manage to be replaced at run-time.  There are many applications
    # for that, such as filtering contacts by site_id (as is done in the
    # authsites app, see github.com/daveycrockett/authsites).
    # This does, however, also make the polling app independent of authsites.
    def __init__(self, data=None, **kwargs):
        if data:
            forms.Form.__init__(self, data, **kwargs)
        else:
            forms.Form.__init__(self, **kwargs)
        if hasattr(Contact, 'groups'):
            self.fields['groups'] = forms.ModelMultipleChoiceField(queryset=Group.objects.all(), required=False)

    def perform(self, request, results):
        groups = self.cleaned_data['groups']
        for c in results:
            for g in groups:
                c.groups.add(g)
