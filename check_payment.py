#!/usr/bin/env python
import sys
sys.path.insert(0, r'C:\Users\Lenovo\lead-qualification-engine-2')

from nirvana import payment as _p

link = _p.payment_link()
print(f"payment_link(): {link}")
print(f"retainer_amount(): {_p.retainer_amount()}")
print(f"retainer_currency(): {_p.retainer_currency()}")