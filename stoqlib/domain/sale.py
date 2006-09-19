# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2005, 2006 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU Lesser General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU Lesser General Public License for more details.
##
## You should have received a copy of the GNU Lesser General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s):   Evandro Vale Miquelito      <evandro@async.com.br>
##              Henrique Romano             <henrique@async.com.br>
##
""" Sale object and related objects implementation """

from decimal import Decimal
from datetime import datetime

from sqlobject import UnicodeCol, DateTimeCol, ForeignKey, IntCol, SQLObject
from stoqdrivers.constants import TAX_ICMS, TAX_NONE, TAX_SUBSTITUTION
from zope.interface import implements
from kiwi.argcheck import argcheck
from kiwi.datatypes import currency

from stoqlib.database.columns import PriceCol, DecimalCol, AutoIncCol
from stoqlib.database.runtime import get_current_user
from stoqlib.lib.validators import get_formatted_price
from stoqlib.lib.defaults import METHOD_GIFT_CERTIFICATE
from stoqlib.lib.translation import stoqlib_gettext
from stoqlib.lib.parameters import sysparam
from stoqlib.exceptions import (SellError, DatabaseInconsistency,
                                StoqlibError)
from stoqlib.domain.renegotiation import (RenegotiationData,
                                          AbstractRenegotiationAdapter)
from stoqlib.domain.base import Domain, BaseSQLView
from stoqlib.domain.sellable import ASellableItem
from stoqlib.domain.fiscal import IssBookEntry, IcmsIpiBookEntry
from stoqlib.domain.payment.base import AbstractPaymentGroup
from stoqlib.domain.product import ProductSellableItem
from stoqlib.domain.service import ServiceSellableItem
from stoqlib.domain.giftcertificate import (GiftCertificateItem,
                                            GiftCertificateAdaptToSellable,
                                            GiftCertificate)
from stoqlib.domain.interfaces import (IContainer, IClient,
                                       IPaymentGroup, ISellable,
                                       IIndividual, ICompany,
                                       IRenegotiationReturnSale)

_ = stoqlib_gettext

#
# Base Domain Classes
#


class GiftCertificateOverpaidSettings:
    """Stores general settings for sale orders with gift certificates and
    when the sum of gift certificate values is greater then the total sale
    amount
    """

    (TYPE_RETURN_MONEY,
     TYPE_GIFT_CERTIFICATE) = range(2)

    renegotiation_type = TYPE_GIFT_CERTIFICATE
    renegotiation_value = Decimal("0.0")
    gift_certificate_number = None


class Sale(Domain):
    """Sale object implementation.

    B{Important attributes}:
        - I{order_number}: an optional identifier for this sale defined by
                           the store.
        - I{open_date}: The day when we started this sale.
        - I{close_date}: The day when we confirmed this sale.
        - I{notes}: Some optional additional information related to this
                    sale.
        - I{till}: The Till operation where this sale lives. Note that every
                   sale and payment generated are always in a till operation
                   which defines a financial history of a store.
        - I{client_role}: This field indicates what client role is tied with
                          the sale order. This is important since a client
                          can have two roles associated, i.e, Individual and/or
                          Company. This is useful when printing the sale
                          invoice.
    """

    implements(IContainer)

    (STATUS_OPENED,
     STATUS_CONFIRMED,
     STATUS_CLOSED,
     STATUS_CANCELLED,
     STATUS_ORDER) = range(5)

    (CLIENT_INDIVIDUAL,
     CLIENT_COMPANY) = range(2)

    statuses = {STATUS_OPENED:      _(u"Opened"),
                STATUS_CONFIRMED:   _(u"Confirmed"),
                STATUS_CLOSED:      _(u"Closed"),
                STATUS_CANCELLED:   _(u"Cancelled"),
                STATUS_ORDER:       _(u"Order")}

    order_number = AutoIncCol('stoqlib_sale_ordernumber_seq')
    coupon_id = IntCol()
    service_invoice_number = IntCol(default=None)
    open_date = DateTimeCol(default=datetime.now)
    close_date = DateTimeCol(default=None)
    confirm_date = DateTimeCol(default=None)
    cancel_date = DateTimeCol(default=None)
    status = IntCol(default=STATUS_OPENED)
    discount_value = PriceCol(default=0)
    surcharge_value = PriceCol(default=0)
    notes = UnicodeCol(default='')
    client_role = IntCol(default=None)

    client = ForeignKey('PersonAdaptToClient', default=None)
    cfop = ForeignKey("CfopData")
    till = ForeignKey('Till')
    salesperson = ForeignKey('ASalesPerson')
    renegotiation_data = ForeignKey("AbstractRenegotiationAdapter",
                                    default=None)


    def _get_percentage_value(self, percentage):
        if not percentage:
            return currency(0)
        subtotal = self.get_sale_subtotal()
        percentage = Decimal(str(percentage))
        perc_value = subtotal * (percentage / Decimal('100.0'))
        return currency(perc_value)

    def _set_discount_by_percentage(self, value):
        """Sets a discount by percentage.
        Note that percentage must be added as an absolute value not as a
        factor like 1.05 = 5 % of surcharge
        The correct form is 'percentage = 3' for a discount of 3 %"""
        self.discount_value = self._get_percentage_value(value)

    def _get_discount_by_percentage(self):
        discount_value = self.discount_value
        if not discount_value:
            return Decimal('0.0')
        subtotal = self.get_sale_subtotal()
        assert subtotal > 0, ('the sale subtotal should not be zero '
                              'at this point')
        total = subtotal - discount_value
        percentage = (1 - total / subtotal) * 100
        return percentage

    discount_percentage = property(_get_discount_by_percentage,
                                   _set_discount_by_percentage)

    def _set_surcharge_by_percentage(self, value):
        """Sets a surcharge by percentage.
        Note that surcharge must be added as an absolute value not as a
        factor like 0.97 = 3 % of discount.
        The correct form is 'percentage = 3' for a surcharge of 3 %"""
        self.surcharge_value = self._get_percentage_value(value)

    def _get_surcharge_by_percentage(self):
        surcharge_value = self.surcharge_value
        if not surcharge_value:
            return Decimal('0.0')
        subtotal = self.get_sale_subtotal()
        assert subtotal > 0, ('the sale subtotal should not be zero '
                              'at this point')
        total = subtotal + surcharge_value
        percentage = ((total / subtotal) - 1) * 100
        return percentage

    surcharge_percentage = property(_get_surcharge_by_percentage,
                                    _set_surcharge_by_percentage)

    #
    # SQLObject hooks
    #

    def _create(self, id, **kw):
        # Sales objects must be set as valid explicitly
        kw['_is_valid_model'] = False
        conn = self.get_connection()
        if not 'cfop' in kw:
            kw['cfop'] = sysparam(conn).DEFAULT_SALES_CFOP
        Domain._create(self, id, **kw)

    #
    # IContainer methods
    #

    # FIXME: Should only accept ASellableItem
    #@argcheck(ASellable)
    def add_item(self, item):
        #item.add_sellable_item(sale=self)
        raise NotImplementedError

    def get_items(self):
        conn = self.get_connection()
        return ASellableItem.selectBy(connection=conn, saleID=self.id)

    @argcheck(ASellableItem)
    def remove_item(self, item):
        conn = self.get_connection()
        table = type(item)
        table.delete(item.id, connection=conn)

    #
    # Classmethods
    #

    @classmethod
    def get_available_sales(cls, conn, till):
        """Returns a list of all available sales for a given
        till operation

        @param conn: a Transaction sqlobject instance
        @param till: a Till instance
        """
        query = cls.q.tillID == till.id
        return cls.select(query, connection=conn)

    @classmethod
    def get_status_name(cls, status):
        if not status in cls.statuses:
            raise DatabaseInconsistency("Invalid status %d" % status)
        return cls.statuses[status]

    #
    # Public API
    #

    @argcheck(Decimal, unicode)
    def add_custom_gift_certificate(self, certificate_value,
                                    certificate_number):
        """This method adds a new custom gift certificate to the current
        sale order.

        @returns: a GiftCertificateAdaptToSellable instance
        """
        conn = self.get_connection()
        cert_type = sysparam(conn).DEFAULT_GIFT_CERTIFICATE_TYPE
        sellable_info = cert_type.base_sellable_info.clone()
        if not sellable_info:
            raise ValueError('A valid gift certificate type must be '
                             'provided at this point')
        sellable_info.price = certificate_value
        certificate = GiftCertificate(connection=conn)
        sellable_cert = certificate.addFacet(ISellable, connection=conn,
                                             barcode=certificate_number,
                                             base_sellable_info=
                                             sellable_info)
        # The new gift certificate which has been created is actually an
        # item of our sale order
        sellable_cert.add_sellable_item(self)
        return sellable_cert

    def get_clone(self):
        from stoqlib.domain.till import Till
        conn = self.get_connection()
        till = Till.get_current(conn)
        return Sale(client_role=self.client_role, client=self.client,
                    cfop=self.cfop, till=till, coupon_id=None,
                    salesperson=self.salesperson, connection=conn)

    def check_payment_group(self):
        group = IPaymentGroup(self)
        if not group:
            raise ValueError("Sale %s doesn't have an IPaymentGroup "
                             "facet at this point" % self)
        return group

    def update_client(self, person):
        # Do not change the name of this method to set_client: this is a
        # callback in SQLObject
        client = IClient(person)
        if not client:
            raise TypeError("%s cannot be adapted to IClient." % person)
        self.client = client

    def reset_discount_and_surcharge(self):
        self.discount_value = self.surcharge_value = currency(0)

    def sell_items(self):
        """Update the stock of all products tied with the current
        sale order
        """
        branch = self.get_till_branch()
        for item in self.get_items():
            if isinstance(item, ProductSellableItem):
                # TODO add support for ordering products, bug #2469
                item.sell(branch)
                continue
            item.sell()

    def cancel_items(self):
        """Restore the stock of all sellable items tied with the current
        sale order
        """
        branch = self.get_till_branch()
        for item in self.get_items():
            if isinstance(item, ProductSellableItem):
                item.cancel(branch)
                continue
            item.cancel(branch)

    def check_close(self):
        """Checks if the payment group has all the payments paid and close
        the group and the sale order
        """
        group = self.check_payment_group()
        if not group.check_close():
            return
        self.close_date = datetime.now()

    def create_sale_return_adapter(self):
        conn = self.get_connection()
        current_user = get_current_user(conn)
        if current_user is None:
            raise StoqlibError("You should have a user for a sale "
                               "renegotiation")
        responsible = current_user.get_adapted()
        group = self.check_payment_group()
        paid_total = group.get_total_paid()
        reg_data = RenegotiationData(connection=conn,
                                     paid_total=paid_total,
                                     invoice_number=None,
                                     responsible=responsible)
        return reg_data.addFacet(IRenegotiationReturnSale, connection=conn)

    @argcheck(AbstractRenegotiationAdapter)
    def cancel(self, renegotiation_adapter):
        rejected = Sale.STATUS_CANCELLED, Sale.STATUS_ORDER
        if self.status in rejected:
            raise StoqlibError("Invalid status for cancel operation, got %s"
                               % Sale.get_status_name(self.status))
        self.cancel_items()
        self.cancel_date = datetime.now()
        group = self.check_payment_group()

        adapted = renegotiation_adapter.get_adapted()
        return_invoice_number = adapted.invoice_number
        group.cancel(return_invoice_number)

        self.renegotiation_data = renegotiation_adapter
        self.status = self.STATUS_CANCELLED

    def validate(self):
        if not self.get_items().count():
            raise SellError('The sale must have sellable items')
        if self.client and not self.client.is_active:
            raise SellError('Unable to make sales for clients with status '
                            '%s' % self.client.get_status_string())
        if not self.status == self.STATUS_OPENED:
            raise SellError('The sale must have STATUS_OPENED for this '
                            'operation, got status %s instead'
                            % self.get_status_name(self.status))
        self.check_payment_group()
        if not self.get_valid():
            self.set_valid()

    @argcheck(GiftCertificateOverpaidSettings)
    def confirm_sale(self, gift_certificate_settings=None):
        self.validate()
        self.sell_items()
        group = IPaymentGroup(self)
        group.confirm(gift_certificate_settings)
        self.status = self.STATUS_CONFIRMED
        self.confirm_date = datetime.now()
        self.check_close()

    #
    # Accessors
    #

    def get_order_number_str(self):
        return u'%05d' % self.order_number

    def get_salesperson_name(self):
        return self.salesperson.get_adapted().name

    def get_client_name(self):
        if not self.client:
            return _(u'Not Specified')
        return self.client.get_name()

    # Warning: "get_client_role" would be a Kiwi accessor here and this is not
    # what we want.
    def get_sale_client_role(self):
        if not self.client:
            return None
        person = self.client.get_adapted()
        if self.client_role is None:
            raise DatabaseInconsistency("The sale %r have a client but no "
                                        "client_role defined." % self)
        elif self.client_role == Sale.CLIENT_INDIVIDUAL:
            individual = IIndividual(person)
            if not individual:
                raise DatabaseInconsistency(
                    "The client_role for sale %r says that the client "
                    "is an individual, but it doesn't have an Individual"
                    " facet" % self)
            return individual
        elif self.client_role == Sale.CLIENT_COMPANY:
            company = ICompany(person)
            if not company:
                raise DatabaseInconsistency(
                    "The client_role for sale %r says that the client is "
                    "a company but it doesn't have a Company facet" % self)
            return company
        else:
            raise DatabaseInconsistency("Invalid client_role for sale %r, "
                                        "got %r" % (self, self.client_role))

    def get_till_branch(self):
        return self.till.station.branch

    def get_sale_subtotal(self):
        subtotal = sum([item.get_total() for item in self.get_items()],
                       currency(0))
        return currency(subtotal)

    def get_total_sale_amount(self):
        """Return the total value paid by the client. This can be
        calculated by:.
        Sale total = Sum(product and service prices) + surcharge +
                     interest - discount"""
        surcharge_value = self.surcharge_value or Decimal('0.0')
        discount_value = self.discount_value or Decimal('0.0')
        subtotal = self.get_sale_subtotal()
        total_amount = subtotal + surcharge_value - discount_value
        return currency(total_amount)

    def get_total_amount_as_string(self):
        return get_formatted_price(self.get_total_sale_amount())

    def get_total_interest(self):
        raise NotImplementedError

    def has_items(self):
        return self.get_items().count() > 0

    def has_products(self):
        return len(self.get_products()) > 0

    def has_services(self):
        return len(self.get_services()) > 0

    def has_gift_certifificates(self):
        return len(self.get_gift_certificates()) > 0

    def get_services(self):
        return [item for item in self.get_items()
                    if isinstance(item, ServiceSellableItem)]

    def get_products(self):
        return [item for item in self.get_items()
                    if isinstance(item, ProductSellableItem)]

    def get_gift_certificates(self):
        """Returns a list of gift certificates tied to the current sale"""
        return [item for item in self.get_items()
                    if isinstance(item, GiftCertificateItem)]

    def get_items_total_quantity(self):
        return sum([item.quantity for item in self.get_items()],
                   Decimal("0.0"))

    def get_items_total_value(self):
        total = sum([item.get_total() for item in self.get_items()],
                   currency(0))
        return currency(total)


#
# Adapters
#


class SaleAdaptToPaymentGroup(AbstractPaymentGroup):

    #
    # IPaymentGroup implementation
    #

    def get_thirdparty(self):
        sale = self.get_adapted()
        client = sale.client
        return client and client.get_adapted() or None

    def set_thirdparty(self, person):
        raise NotImplementedError

    def get_group_description(self):
        sale = self.get_adapted()
        return _(u'sale %s') % sale.get_order_number_str()

    #
    # Auxiliar methods
    #

    def get_gift_certificates(self):
        conn = self.get_connection()
        table = GiftCertificateAdaptToSellable
        return table.selectBy(groupID=self.id, connection=conn)

    def confirm_gift_certificates(self):
        """Update gift certificates of the current sale, setting their
        status properly.
        """
        if not self.default_method == METHOD_GIFT_CERTIFICATE:
            return
        for item in self.get_gift_certificates():
            item.apply_as_payment_method()

    def get_pm_commission_total(self):
        """Return the payment method commission total. Usually credit
        card payment method is the most common method which uses
        commission
        """
        return currency(0)

    def get_total_received(self):
        """Return the total amount paid by the client (sale total)
        deducted of payment method commissions"""
        sale = self.get_adapted()
        return sale.get_total_sale_amount() - self.get_pm_commission_total()

    @argcheck(Decimal)
    def _get_icms_total(self, av_difference):
        """A Brazil-specific method
        Calculates the icms total value

        @param av_difference: the average difference for the sale items.
                              it means the average discount or surcharge
                              applied over all sale items
        """
        icms_total = Decimal("0.0")
        conn = self.get_connection()
        icms_tax = sysparam(conn).ICMS_TAX / Decimal("100.0")
        sale = self.get_adapted()
        for item in sale.get_products():
            price = item.price + av_difference
            sellable = item.sellable
            if (sellable.tax_type == TAX_SUBSTITUTION or
                sellable.tax_type == TAX_NONE):
                continue
            elif sellable.tax_type == TAX_ICMS:
                icms_total += icms_tax * (price * item.quantity)
            else:
                raise ValueError("Invalid tax type for product %s. "
                                 "Got %d" % (sellable, sellable.tax_type))
        return icms_total

    @argcheck(Decimal)
    def _get_iss_total(self, av_difference):
        """A Brazil-specific method
        Calculates the iss total value

        @param av_difference: the average difference for the sale items.
                              it means the average discount or surcharge
                              applied over all sale items
        """
        iss_total = Decimal('0.0')
        conn = self.get_connection()
        iss_tax = sysparam(conn).ISS_TAX / Decimal("100.0")
        sale = self.get_adapted()
        for item in sale.get_services():
            price = item.price + av_difference
            iss_total += iss_tax * (price * item.quantity)
        return iss_total

    def _has_iss_entry(self):
        return IssBookEntry.has_entry_by_payment_group(
                                     self.get_connection(), self)

    def _has_icms_entry(self):
        return IcmsIpiBookEntry.has_entry_by_payment_group(
                                            self.get_connection(), self)

    def _get_average_difference(self):
        sale = self.get_adapted()
        if not sale.has_items():
            raise DatabaseInconsistency("Sale orders must have items, which "
                                        "means products or services or gift "
                                        "certificates")
        total_quantity = sale.get_items_total_quantity()
        if not total_quantity:
            raise DatabaseInconsistency("Sale total quantity should never "
                                        "be zero")
        # If there is a discount or a surcharge applied in the whole total
        # sale amount, we must share it between all the item values
        # otherwise the icms and iss won't be calculated properly
        total = (sale.get_total_sale_amount() -
                 self.get_pm_commission_total())
        subtotal = sale.get_sale_subtotal()
        return (total - subtotal) / total_quantity

    def _get_iss_entry(self):
        return IssBookEntry.get_entry_by_payment_group(
                                        self.get_connection(), self)

    def update_iss_entries(self):
        """Update iss entries after printing a service invoice"""
        av_difference = self._get_average_difference()
        sale = self.get_adapted()
        if not self._has_iss_entry():
            iss_total = self._get_iss_total(av_difference)
            self.create_iss_book_entry(sale.cfop,
                                       sale.service_invoice_number,
                                       iss_total)
            return

        conn = self.get_connection()
        iss_entry = IssBookEntry.get_entry_by_payment_group(conn, self)
        if iss_entry.invoice_number == sale.service_invoice_number:
            return

        # User just cancelled the old invoice and would like to print a
        # new one -> reverse old entry and create a new one
        iss_entry.reverse_entry(iss_entry.invoice_number)

        # create the new iss entry
        iss_total = self._get_iss_total(av_difference)
        self.create_iss_book_entry(sale.cfop,
                                   sale.service_invoice_number,
                                   iss_total)

    def _create_fiscal_entries(self):
        """A Brazil-specific method
        Create new ICMS and ISS entries in the fiscal book
        for a given sale.

        Important: freight and interest are not part of the base value for
        ICMS. Only product values and surcharge which applies increasing the
        product totals are considered here.

        Note that we are not calculating ICMS or ISS for gift certificates since
        it will be calculated for the products sold when using gift
        certificates as payment methods.
        """
        sale = self.get_adapted()
        av_difference = self._get_average_difference()

        if sale.has_products():
            icms_total = self._get_icms_total(av_difference)
            self.create_icmsipi_book_entry(sale.cfop, sale.coupon_id,
                                           icms_total)

        if sale.has_services() and sale.service_invoice_number:
            iss_total = self._get_iss_total(av_difference)
            self.create_iss_book_entry(sale.cfop,
                                       sale.service_invoice_number,
                                       iss_total)

    @argcheck(GiftCertificateOverpaidSettings)
    def _setup_gift_certificate_overpaid_value(self,
                                               gift_certificate_settings):
        regtype = gift_certificate_settings.renegotiation_type
        overpaid_value = gift_certificate_settings.renegotiation_value
        number = gift_certificate_settings.gift_certificate_number

        order = self.get_adapted()
        if regtype == GiftCertificateOverpaidSettings.TYPE_RETURN_MONEY:
            order_number = order.order_number
            reason = _(u'1/1 Money returned for gift certificate '
                        'acquittance on sale %04d' % order_number)
            self.create_debit(overpaid_value, reason,
                              self.get_adapted().till)

        elif (regtype ==
              GiftCertificateOverpaidSettings.TYPE_GIFT_CERTIFICATE):
            sellable_cert = order.add_custom_gift_certificate(overpaid_value,
                                                              number)
            sellable_cert.sell()

        else:
            raise StoqlibError("Invalid type for "
                               "GiftCertificateOverpaidSettings instance "
                               "got %s" % regtype)

    @argcheck(GiftCertificateOverpaidSettings)
    def confirm(self, gift_certificate_settings=None):
        """Validate the current payment group, create payments and setup the
        associated gift certificates properly.
        """
        has_overpaid = gift_certificate_settings is not None
        if not has_overpaid:
            self.setup_inpayments()
            self.confirm_money_payments()
        self.confirm_gift_certificates()
        self._create_fiscal_entries()
        if gift_certificate_settings is None:
            return
        # Here we have the payment method set as gift certificate but there
        # is an overpaid value to deal with.
        self._setup_gift_certificate_overpaid_value(gift_certificate_settings)

    #
    # AbstractPaymentGroup hooks
    #

    def get_default_payment_method(self):
        return self.default_method

Sale.registerFacet(SaleAdaptToPaymentGroup, IPaymentGroup)


#
# Views
#


class SaleView(SQLObject, BaseSQLView):
    """Stores general informatios about sales"""
    coupon_id = IntCol()
    order_number = IntCol()
    open_date = DateTimeCol()
    close_date = DateTimeCol()
    confirm_date = DateTimeCol()
    cancel_date = DateTimeCol()
    status = IntCol()
    notes = UnicodeCol()
    salesperson_name = UnicodeCol()
    client_name = UnicodeCol()
    client_id = IntCol()
    surcharge_value = PriceCol()
    discount_value = PriceCol()
    subtotal = PriceCol()
    total = PriceCol()
    total_quantity = DecimalCol()

    def get_client_name(self):
        return self.client_name or u""

    def get_order_number_str(self):
        return u"%05d" % self.order_number

    def get_open_date_as_string(self):
        return self.open_date.strftime("%x")

    def get_status_name(self):
        return Sale.get_status_name(self.status)
