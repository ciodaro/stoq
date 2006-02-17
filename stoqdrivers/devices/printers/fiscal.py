# -*- Mode: Python; coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Stoqdrivers
## Copyright (C) 2005 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307,
## USA.
##
## Author(s):   Johan Dahlin     <jdahlin@async.com.br>
##              Henrique Romano  <henrique@async.com.br>
##

import gettext
from decimal import Decimal

from kiwi.argcheck import number, percent

from stoqdrivers.exceptions import (CloseCouponError, PaymentAdditionError,
                                    PendingReadX, PendingReduceZ,
                                    CouponOpenError)
from stoqdrivers.constants import (TAX_NONE,TAX_IOF, TAX_ICMS, TAX_SUBSTITUTION,
                                   TAX_EXEMPTION, UNIT_EMPTY, UNIT_LITERS,
                                   UNIT_WEIGHT, UNIT_METERS, MONEY_PM, CHEQUE_PM,
                                   UNIT_CUSTOM)
from stoqdrivers.devices.printers.base import BasePrinter
from stoqdrivers.devices.printers.capabilities import capcheck
from stoqdrivers.utils import encode_text

_ = lambda msg: gettext.dgettext("stoqdrivers", msg)

#
# Extra data types to argcheck
#

class taxcode(number):
    @classmethod
    def value_check(cls, name, value):
        if value not in (TAX_NONE, TAX_IOF, TAX_ICMS, TAX_SUBSTITUTION,
                         TAX_EXEMPTION):
            raise ValueError("%s must be one of TAX_* constants" % name)

class unit(number):
    @classmethod
    def value_check(cls, name, value):
        if value not in (UNIT_WEIGHT, UNIT_METERS, UNIT_LITERS,
                         UNIT_EMPTY, UNIT_CUSTOM):
            raise ValueError("%s must be one of UNIT_* constants" % name)

class payment_method(number):
    @classmethod
    def value_check(cls, name, value):
        if value not in (MONEY_PM, CHEQUE_PM):
            raise ValueError("%s must be one of *_PM constants" % name)

#
# FiscalPrinter interface
#

class FiscalPrinter(BasePrinter):
    def __init__(self, brand=None, model=None, device=None, config_file=None):
        BasePrinter.__init__(self, brand, model, device, config_file)
        self.has_been_totalized = False
        self.totalized_value = self.payments_total_value = 0.0
        self._capabilities = self._driver.get_capabilities()
        self._charset = self._driver.coupon_printer_charset

    def get_capabilities(self):
        return self._capabilities

    def _format_text(self, text):
        return encode_text(text, self._charset)

    @capcheck(basestring, basestring, basestring)
    def identify_customer(self, customer_name, customer_address, customer_id):
        self.info('identify_customer')
        self._driver.coupon_identify_customer(
            self._format_text(customer_name),
            self._format_text(customer_address),
            self._format_text(customer_id))

    def open(self):
        self.info('coupon_open')
        return self._driver.coupon_open()

    @capcheck(basestring, number, number, unit, basestring, taxcode, percent,
              percent, basestring)
    def add_item(self, item_code, items_quantity, item_price, unit,
                 item_description, taxcode, discount, charge, unit_desc=''):
        if discount and charge:
            raise TypeError("discount and charge can not be used together")
        elif unit != UNIT_CUSTOM and unit_desc:
            raise ValueError("You can't specify the unit description if "
                             "you aren't using UNIT_CUSTOM constant.")
        elif unit == UNIT_CUSTOM and not unit_desc:
            raise ValueError("You must specify the unit description when "
                             "using UNIT_CUSTOM constant.")
        elif unit == UNIT_CUSTOM and len(unit_desc) != 2:
            raise ValueError("unit description must be 2-byte sized string")
        self.info('coupon_add_item')
        return self._driver.coupon_add_item(
            self._format_text(item_code), items_quantity, item_price, unit,
            self._format_text(item_description), taxcode, discount, charge,
            unit_desc=self._format_text(unit_desc))

    @capcheck(percent, percent, taxcode)
    def totalize(self, discount, charge, taxcode):
        if discount and charge:
            raise TypeError("discount and charge can not be used together")

        self.info('coupon_totalize')
        result = self._driver.coupon_totalize(discount, charge, taxcode)
        self.has_been_totalized = True
        self.totalized_value = result
        return result

    @capcheck(payment_method, number, basestring)
    def add_payment(self, payment_method, payment_value, payment_description=''):
        self.info('coupon_add_payment')
        if not self.has_been_totalized:
            raise PaymentAdditionError(_("You must totalize the coupon "
                                         "before add payments."))
        result = self._driver.coupon_add_payment(
            payment_method, payment_value,
            self._format_text(payment_description))
        self.payments_total_value += payment_value
        return result

    def cancel(self):
        self.info('coupon_cancel')
        return self._driver.coupon_cancel()

    @capcheck(int)
    def cancel_item(self, item_id):
        self.info('coupon_cancel_item')
        return self._driver.coupon_cancel_item(item_id)

    @capcheck(basestring)
    def close(self, promotional_message=''):
        self.info('coupon_close')
        if not self.has_been_totalized:
            raise CloseCouponError(_("You must totalize the coupon before "
                                     "closing it"))
        elif self.totalized_value > self.payments_total_value:
            raise CloseCouponError(_("Isn't possible close the coupon since "
                                     "the payments total (%.2f) doesn't "
                                     "match the totalized value (%.2f).")
                                   % (self.payments_total_value,
                                      self.totalized_value))
        else:
            return self._driver.coupon_close(
                self._format_text(promotional_message))

    def summarize(self):
        self.info('summarize')
        return self._driver.summarize()

    def close_till(self):
        self.info('close_till')
        return self._driver.close_till()

    @capcheck(number)
    def till_add_cash(self, add_cash_value):
        self.info('till_add_cash')
        return self._driver.till_add_cash(add_cash_value)

    @capcheck(number)
    def till_remove_cash(self, remove_cash_value):
        self.info('till_remove_cash')
        return self._driver.till_remove_cash(remove_cash_value)

    def get_status(self):
        self.info('get_status')
        return self._driver.get_status()

def test():
    p = FiscalPrinter()

    p.identify_customer('Henrique Romano', 'Async', '1234567890')
    while True:
        try:
            p.open()
            break
        except CouponOpenError:
            p.cancel()
        except PendingReadX:
            p.summarize()
            return
        except PendingReduceZ:
            p.close_till()
            return
    i1 = p.add_item("123456", 2, 10.00, UNIT_CUSTOM, u"Hollywóód", TAX_NONE, 0,
                    0, unit_desc=u"mç")
    i2 = p.add_item("654321", 5, 1.53, UNIT_LITERS, "Bohemia Beer", TAX_NONE,
                    0, 0)
    p.cancel_item(i1)
    coupon_total = p.totalize(1.0, 0, TAX_NONE)
    p.add_payment(MONEY_PM, 2.00, '')
    p.add_payment(MONEY_PM, 11.00, '')
    coupon_id = p.close()
    print "+++ coupon %d created." % coupon_id

if __name__ == '__main__':
    test()
