#!/usr/bin/python
# -*- coding: utf-8 -*-
import logging
from django import forms
from rapidsms.models import Contact, Connection
from django.contrib.auth.models import Group
from django.db.models import Q
from rapidsms_httprouter.models import Message
from rapidsms.messages.outgoing import OutgoingMessage
from generic.forms import ActionForm, FilterForm
from contact.models import MassText, Flag
from django.contrib.sites.models import Site
from rapidsms.contrib.locations.models import Location
from django.conf import settings
import datetime
from django.core.exceptions import FieldError
from django.utils.safestring import mark_safe

logger = logging.getLogger(__name__)



class SMSInput(forms.Textarea):
    """ A widget for sms input """

    def __init__(self, *args, **kwargs):
        super(SMSInput, self).__init__(*args, **kwargs)

    def render(self, name, value, attrs=None):
        javascript = """
         <script type="text/javascript">
            //<![CDATA[
            function count_characters(name,counter_container,submit_btn)
        {

        var el="[name='"+name+"']"

        var elem= $(el);

        var value = elem.val();
        var count = value.length;
        //regex for stripping the spaces
        var regex = new RegExp(/^\s*|\s*$/g);
        var chars_left = 160 - count;
        if (chars_left >= 0) {
          if (elem.is('.overlimit')) {
            elem.removeClass("overlimit");
          }

          if (chars_left > 1) {
            str = (chars_left) + " characters left";
          }
          else if (chars_left > 0) {
            str = "1 character left";
          }
          else {
            str = "No characters left";
          }
        } else {
          if (!elem.is('.overlimit')) {
            elem.addClass("overlimit");
          }

          if (chars_left < -1) {
            str = -chars_left + " characters over limit";
          }
          else {
            str = "1 character over limit";
          }
        }
        var ok = (count > 0 && count < 161) && (value.replace(regex,"") != elem._value);

        $(submit_btn).disabled = !ok;
        elem.next().html(str);
        }
        $(".smsinput").change(setInterval(function() {count_characters('%(name)s','.counter','foo');},500));

             //]]>
        </script>

        """%{'name':name}
        style = """
        width: 18em;
        height: 56px;
        border: 1px solid #CCCCCC;
        color: #222222;
        font: 14px/18px "Helvetica Neue",Arial,sans-serif;
        outline: medium none;
        overflow-x: hidden;
        overflow-y: auto;
        padding: 2px;
        white-space: pre-wrap;
        word-wrap: break-word;

        """
        attrs = {'style':style}
        attrs['class'] = "smsinput"
        return mark_safe(
                "%s<div class='counter' ></div>" % super(SMSInput, self).render(name, value, attrs) + javascript)




class FlaggedMessageForm(forms.ModelForm):
    class Meta:
        model = Flag
        fields = ('name', 'rule', 'words',)


class ReplyForm(forms.Form):
    recipient = forms.CharField(max_length=20)
    message = forms.CharField(max_length=160, widget=forms.TextInput(attrs={'size': '60'}))
    in_response_to = forms.ModelChoiceField(queryset=Message.objects.filter(direction='I'), widget=forms.HiddenInput())

    def clean(self):
        cleaned_data = self.cleaned_data
        text = cleaned_data.get('message')

        #replace common MS-word characters with SMS-friendly characters
        for find, replace in [(u'\u201c', '"'),
                              (u'\u201d', '"'),
                              (u'\u201f', '"'),
                              (u'\u2018', "'"),
                              (u'\u2019', "'"),
                              (u'\u201B', "'"),
                              (u'\u2013', "-"),
                              (u'\u2014', "-"),
                              (u'\u2015', "-"),
                              (u'\xa7', "$"),
                              (u'\xa1', "i"),
                              (u'\xa4', ''),
                              (u'\xc4', 'A')]:
            cleaned_data['message'] = text.replace(find, replace)
            cleaned_data['message'] = text.replace('%', '%%')
        return cleaned_data


class FilterGroupsForm(FilterForm):
    """ concrete implementation of filter form """
    # This may seem like a hack, but this allows time for the Contact model's
    # default manage to be replaced at run-time.  There are many applications
    # for that, such as filtering contacts by site_id (as is done in the
    # authsites app, see github.com/daveycrockett/authsites).
    # This does, however, also make the polling app independent of authsites.
    def __init__(self, data=None, **kwargs):
        self.request = kwargs.pop('request')
        if data:
            forms.Form.__init__(self, data, **kwargs)
        else:
            forms.Form.__init__(self, **kwargs)
        if hasattr(Contact, 'groups'):
            choices = ((-1, 'No Group'),) + tuple([(int(g.pk), g.name) for g in Group.objects.all().order_by('name')])
            self.fields['groups'] = forms.MultipleChoiceField(choices=choices, required=True)

    def filter(self, request, queryset):
        groups_pk = self.cleaned_data['groups']
        if '-1' in groups_pk:

            groups_pk.remove('-1')
            if len(groups_pk):
                try:
                    return queryset.filter(Q(groups=None) | Q(groups__in=groups_pk))
                except FieldError:
                    q = Q(group=None)
                    for f in Group.objects.filter(pk__in=groups_pk).values_list('name', flat=True):
                        q = q | Q(group__icontains=f)

                    return queryset.filter(q)
            else:
                try:
                    return queryset.filter(groups=None)
                except FieldError:
                    return queryset.filter(group=None)

        else:
            try:
                return queryset.filter(groups__in=groups_pk)
            except FieldError:
                q = None
                for f in Group.objects.filter(pk__in=groups_pk).values_list('name', flat=True):
                    if not q:
                        q = Q(group__iregex="\m%s\y" % f)
                    else:
                        q = q | Q(group__iregex="\m%s\y" % f)
                return queryset.filter(q)


class NewContactForm(forms.ModelForm):
    class Meta:
        model = Contact


class FreeSearchForm(FilterForm):
    """ concrete implementation of filter form
        TO DO: add ability to search for multiple search terms separated by 'or'
    """

    searchx = forms.CharField(max_length=100, required=False, label="Free-form search",
                              help_text="Use 'or' to search for multiple names")

    def filter(self, request, queryset):
        searchx = self.cleaned_data['searchx'].strip()
        if searchx == "":
            return queryset
        elif searchx[0] in ["'", '"'] and searchx[-1] in ["'", '"']:
            searchx = searchx[1:-1]
            return queryset.filter(Q(name__iregex=".*\m(%s)\y.*" % searchx)
                                   | Q(reporting_location__name__iregex=".*\m(%s)\y.*" % searchx)
                                   | Q(connection__identity__iregex=".*\m(%s)\y.*" % searchx))

        else:
            return queryset.filter(Q(name__icontains=searchx)
                                   | Q(reporting_location__name__icontains=searchx)
                                   | Q(connection__identity__icontains=searchx))


class FreeSearchForm2(FilterForm):
    """ concrete implementation of filter form
        TO DO: add ability to search for multiple search terms separated by 'or'
    """

    searchx = forms.CharField(max_length=100, required=False, label="Free-form search",
                              help_text="Use 'or' to search for multiple names")

    def filter(self, request, queryset):
        searchx = self.cleaned_data['searchx'].strip()
        if searchx == "":
            return queryset
        elif searchx[0] in ["'", '"'] and searchx[-1] in ["'", '"']:
            searchx = searchx[1:-1]
            return queryset.filter(Q(name__iregex=".*\m(%s)\y.*" % searchx)
                                   | Q(loc_name__iregex=".*\m(%s)\y.*" % searchx)
                                   | Q(connections__iregex=".*\m(%s)\y.*" % searchx))

        else:
            return queryset.filter(Q(name__icontains=searchx)
                                   | Q(loc_name__icontains=searchx)
                                   | Q(connections__icontains=searchx))


class FreeSearchTextForm(FilterForm):
    """ concrete implementation of filter form """

    search = forms.CharField(max_length=100, required=True, label="Free-form search",
                             #help_text="Use 'or' to search for multiple names",
                             widget=forms.TextInput(attrs={'class': 'itext', 'size': 14}))

    def filter(self, request, queryset):
        search = self.cleaned_data['search']
        return queryset.filter(text__icontains=search)


class HandledByForm(FilterForm):
    type = forms.ChoiceField(
        choices=(('', '-----'), ('poll', 'Poll Response'), ('rapidsms_xforms', 'Report'), ('*', 'Other'),), \
        required=False)

    def filter(self, request, queryset):
        handled_by = self.cleaned_data['type']
        if handled_by == '':
            return queryset
        elif handled_by == '*':
            return queryset.filter(application=None)
        else:
            return queryset.filter(application=handled_by)


class DistictFilterForm(FilterForm):
    """ filter cvs districs on their districts """

    district2 = forms.ChoiceField(label="District", choices=(('', '-----'), (-1,
                                                                             'No District')) + tuple([(int(d.pk),
                                                                                                       d.name) for d in
                                                                                                      Location.objects.filter(
                                                                                                          type__slug='district'
                                                                                                      ).order_by(
                                                                                                          'name')]),
                                  required=False,
                                  widget=forms.Select({'onchange': 'update_district2(this)'}))


    def filter(self, request, queryset):
        district_pk = self.cleaned_data['district2']
        if district_pk == '':
            return queryset
        elif int(district_pk) == -1:
            return queryset.filter(reporting_location__in=Location.objects.filter(type__in=['country', 'region']))
        else:

            try:
                district = Location.objects.get(pk=district_pk)
            except Location.DoesNotExist:
                district = None
            if district:
                return queryset.filter(district=district.name)
                #return queryset.filter(reporting_location__in=district.get_descendants(include_self=True))
            else:
                return queryset


class RolesFilter(FilterForm):
    role = forms.ChoiceField(choices=(('', '----'),) + tuple(
        [(int(g.id), g.name) for g in Group.objects.all().order_by('name')]), required=False)

    def filter(self, request, queryset):
        group_pk = self.cleaned_data['role']
        if group_pk == '':
            return queryset
        else:
            try:
                grp = Group.objects.get(pk=group_pk)
            except Group.DoesNotExist:
                grp = None
            if grp:
                #return queryset.filter(groups__pk__in=[grp.pk])
                if grp.name == 'VHT':
                    return queryset.filter(groups__contains=grp.name).exclude(groups__contains='PVHT')
                else:
                    return queryset.filter(groups__contains=grp.name)
            return queryset


class MultipleDistictFilterForm(FilterForm):
    districts = forms.ModelMultipleChoiceField(queryset=
                                               Location.objects.filter(type__slug='district'
                                               ).order_by('name'), required=False)


    def filter(self, request, queryset):
        districts = self.cleaned_data['districts']
        if len(districts):
            return queryset.filter(reporting_location__in=districts)
        else:
            return queryset


class DistictFilterMessageForm(FilterForm):
    """ filter cvs districs on their districts """
    district = forms.ChoiceField(choices=(('', '-----'), (-1,
                                                          'No District')) + tuple([(int(d.pk),
                                                                                    d.name) for d in
                                                                                   Location.objects.filter(
                                                                                       type__slug='district'
                                                                                   ).order_by('name')]), required=False)


    def filter(self, request, queryset):
        district_pk = self.cleaned_data['district']
        if district_pk == '':
            return queryset
        elif int(district_pk) == -1:
            return queryset.filter(Q(connection__contact=None) | Q(connection__contact__reporting_location=None))
        else:
            try:
                district = Location.objects.get(pk=district_pk)
            except Location.DoesNotExist:
                district = None
            if district:
                return queryset.filter(
                    connection__contact__reporting_location__in=district.get_descendants(include_self=True))
            else:
                return queryset


class MassTextForm(ActionForm):
    text = forms.CharField(max_length=160, required=True, widget=SMSInput())
    action_label = 'Send Message'

    def clean_text(self):
        text = self.cleaned_data['text']

        #replace common MS-word characters with SMS-friendly characters
        for find, replace in [(u'\u201c', '"'),
                              (u'\u201d', '"'),
                              (u'\u201f', '"'),
                              (u'\u2018', "'"),
                              (u'\u2019', "'"),
                              (u'\u201B', "'"),
                              (u'\u2013', "-"),
                              (u'\u2014', "-"),
                              (u'\u2015', "-"),
                              (u'\xa7', "$"),
                              (u'\xa1', "i"),
                              (u'\xa4', ''),
                              (u'\xc4', 'A')]:
            text = text.replace(find, replace)
        return text

    def perform(self, request, results):
        if type(results).__name__ != 'QuerySet':
            results = Contact.objects.filter(pk__in=request.REQUEST.get('results', ""))
        if results is None or len(results) == 0:
            return 'A message must have one or more recipients!', 'error'

        if request.user and request.user.has_perm('contact.can_message'):
            if type(results[0]).__name__ == 'Reporters':

                con_ids = \
                    [r.default_connection.split(',')[1] if len(r.default_connection.split(',')) > 1 else 0 for r in
                     results]
                connections = list(Connection.objects.filter(pk__in=con_ids).distinct())
                contacts = list(Contact.objects.filter(pk__in=results.values_list('id', flat=True)))
                print contacts
            else:
                connections = \
                    list(Connection.objects.filter(contact__pk__in=results.values_list('id', flat=True)).distinct())
                contacts = list(results)
            text = self.cleaned_data.get('text', "")
            text = text.replace('%', u'\u0025')
            messages = Message.mass_text(text, connections)

            MassText.bulk.bulk_insert(send_pre_save=False,
                                      user=request.user,
                                      text=text,
                                      contacts=contacts)
            masstexts = MassText.bulk.bulk_insert_commit(send_post_save=False, autoclobber=True)
            masstext = masstexts[0]
            if settings.SITE_ID:
                masstext.sites.add(Site.objects.get_current())

            return 'Message successfully sent to %d numbers' % len(connections), 'success',
        else:
            return "You don't have permission to send messages!", 'error',


class ReplyTextForm(ActionForm):
    text = forms.CharField(required=True, widget=SMSInput())
    action_label = 'Reply to selected'

    def perform(self, request, results):
        if results is None or len(results) == 0:
            return ('A message must have one or more recipients!', 'error')

        if request.user and request.user.has_perm('contact.can_message'):
            router = get_router()
            text = self.cleaned_data['text']
            for msg in results:
                outgoing = OutgoingMessage(msg.connection, text)
                router.handle_outgoing(outgoing, msg)
            return ('%d messages sent successfully' % results.count(), 'success',)
        else:
            return ("You don't have permission to send messages!", 'error',)


class AssignGroupForm(ActionForm):
    action_label = 'Assign to group(s)'

    # This may seem like a hack, but this allows time for the Contact model's
    # default manage to be replaced at run-time.  There are many applications
    # for that, such as filtering contacts by site_id (as is done in the
    # authsites app, see github.com/daveycrockett/authsites).
    # This does, however, also make the polling app independent of authsites.
    def __init__(self, data=None, **kwargs):
        self.request = kwargs.pop('request')
        if data:
            forms.Form.__init__(self, data, **kwargs)
        else:
            forms.Form.__init__(self, **kwargs)
        if hasattr(Contact, 'groups'):
            if self.request.user.is_authenticated():
                self.fields['groups'] = forms.ModelMultipleChoiceField(
                    queryset=Group.objects.filter(pk__in=self.request.user.groups.values_list('pk', flat=True)),
                    required=False)
            else:
                self.fields['groups'] = forms.ModelMultipleChoiceField(queryset=Group.objects.all(), required=False)

    def perform(self, request, results):
        groups = self.cleaned_data['groups']
        contacts = Contact.objects.filter(pk__in=results)
        for c in contacts:
            for g in groups:
                c.groups.add(g)
        return ('%d Contacts assigned to %d groups.' % (len(results), len(groups)), 'success',)


class RemoveGroupForm(ActionForm):
    action_label = 'Remove  group(s)'

    def __init__(self, data=None, **kwargs):
        self.request = kwargs.pop('request')
        if data:
            forms.Form.__init__(self, data, **kwargs)
        else:
            forms.Form.__init__(self, **kwargs)
        if hasattr(Contact, 'groups'):
            if self.request.user.is_authenticated():
                self.fields['groups'] = forms.ModelMultipleChoiceField(
                    queryset=Group.objects.filter(pk__in=self.request.user.groups.values_list('pk', flat=True)),
                    required=False)
            else:
                self.fields['groups'] = forms.ModelMultipleChoiceField(queryset=Group.objects.all(), required=False)

    def perform(self, request, results):
        groups = self.cleaned_data['groups']
        contacts = Contact.objects.filter(pk__in=results)
        for c in contacts:
            for g in groups:
                c.groups.remove(g)
        return ('%d Contacts removed from %d groups.' % (len(results), len(groups)), 'success',)


class FlaggedForm(FilterForm):
    """ filter flagged/unflagged messages form """

    flagged = forms.ChoiceField(choices=(('', '-----'), (1, 'Flagged'), (0, 'Not flagged'),))

    def filter(self, request, queryset):
        flagged = self.cleaned_data['flagged']
        if flagged == '':
            return queryset
        elif int(flagged) == 1:
            return queryset.exclude(flags=None)
        else:
            return queryset.filter(flags=None)


class FlagMessageForm(ActionForm):
    """ flag/unflag messages action form """

    flag = forms.ChoiceField(choices=(('', '-----'), ('flag', 'Flag'), ('unflag', 'Unflag'),))
    action_label = 'Flag/Unflag selected'

    def perform(self, request, results):
        if results is None or len(results) == 0:
            return ('You must select one or more messages to Flag or Unflag them!', 'error')
        flag = self.cleaned_data['flag']
        for msg in results:
            if flag == 'flag':
                msg.flags.create()
            else:
                for msg_flag in msg.flags.all():
                    msg_flag.delete()
        return ('%d message(s) have been %sed' % (len(results), flag), 'successfully!',)


class GenderFilterForm(FilterForm):
    """ filter contacts by their gender"""

    gender = forms.ChoiceField(choices=(('', '-----'), ('M', 'Male'), ('F', 'Female'), ('None', 'N/A')))

    def filter(self, request, queryset):

        gender = self.cleaned_data['gender']
        if gender == '':
            return queryset
        elif gender == 'M':
            return queryset.filter(gender='M')
        elif gender == 'F':
            return queryset.filter(gender='F')
        else:
            return queryset.filter(gender=None)


class AgeFilterForm(FilterForm):
    """ filter contacts by their age """
    flag = forms.ChoiceField(label='', choices=(('', '-----'), ('==', 'Equal to'), ('>', 'Greater than'), ('<', \
                                                                                                           'Less than'),
                                                ('None', 'N/A')), required=False)
    age = forms.CharField(max_length=20, label="Age", widget=forms.TextInput(attrs={'size': '20'}), required=False)

    def filter(self, request, queryset):

        flag = self.cleaned_data['flag']
        try:
            age = int(self.cleaned_data['age'])
            end = datetime.datetime.now()
            start = end - datetime.timedelta(days=age * 365)
        except ValueError:
            age = None
            start = None
            end = None

        if flag == '':
            return queryset
        elif flag == '==':
            return queryset.exclude(birthdate=None).filter(birthdate__year=start.year)
        elif flag == '>':
            return queryset.exclude(birthdate=None).exclude(birthdate__range=(start, end))
        elif flag == "<":
            return queryset.exclude(birthdate=None).filter(birthdate__range=(start, end))
        else:
            return queryset.filter(birthdate=None)
