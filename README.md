**This python package is unofficial and is not related in any way to Lidl. It was developed by reversed engineered requests and can stop working at anytime!**

> **Note:** This is a fork of [Andre0512/lidl-plus](https://github.com/Andre0512/lidl-plus) with fixes for Lidl's API changes (July 2026):
> - new login page (email or phone number + new selectors)
> - coupons moved to `coupons.lidlplus.com/app/api/v2/promotionsList`
> - single receipts moved to the v3 ticket API (receipt content is now html in `htmlPrintedReceipt`)
> - dependency pins so `selenium-wire` still works (`blinker`, `pyOpenSSL`, `setuptools`)
>
> The `loyalty_id` endpoint was removed by Lidl and currently returns 404.
> Install from this repo, the version on PyPI is outdated:
> ```bash
> pip install "lidl-plus[auth] @ git+https://github.com/mel525/lidl-plus"
> ```

# Python Lidl Plus API
[![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/Andre0512/lidl-plus/python-check.yml?branch=main&label=checks)](https://github.com/Andre0512/lidl-plus/actions/workflows/python-check.yml)
[![PyPI - Status](https://img.shields.io/pypi/status/lidl-plus)](https://pypi.org/project/lidl-plus)
[![PyPI](https://img.shields.io/pypi/v/lidl-plus?color=blue)](https://pypi.org/project/lidl-plus)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/lidl-plus)](https://www.python.org/)
[![PyPI - License](https://img.shields.io/pypi/l/lidl-plus)](https://github.com/Andre0512/lidl-plus/blob/main/LICENCE)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/lidl-plus)](https://pypistats.org/packages/lidl-plus)
[![Buy Me a Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-orange.svg)](https://www.buymeacoffee.com/andre0512)  


Fetch receipts and more from Lidl Plus.
## Installation
```bash
pip install lidl-plus
```

## Authentication
To login in Lidl Plus we need to simulate the app login.
This is a bit complicated, we need a web browser and some additional python packages.
After we have received the token once, we can use it for further requestes and we don't need a browser anymore.

#### Prerequisites
* Check you have installed one of the supported web browser
  - Chromium
  - Google Chrome
  - Mozilla Firefox
  - Microsoft Edge
* Install additional python packages
  ```bash
  pip install "lidl-plus[auth]"
  ```
#### Commandline-Tool
```bash
$ lidl-plus auth
Enter your language (de, en, ...): de
Enter your country (DE, AT, ...): AT
Login with [e]mail or [p]hone number?: p
Enter your lidl plus phone number (+4912345...): +4915784632296
Enter your lidl plus password:
Enter the verify code you received via phone: 590287
------------------------- refresh token ------------------------
2D4FC2A699AC703CAB8D017012658234917651203746021A4AA3F735C8A53B7F
----------------------------------------------------------------
```

#### Python
```python
from lidlplus import LidlPlusApi

lidl = LidlPlusApi(language="de", country="AT")
lidl.login("+4915784632296", "password", method="phone", verify_token_func=lambda: input("Insert code: "))
print(lidl.refresh_token)
```
Use `method="email"` (the default) to log in with your email address instead.
## Usage
Currently, the only features are fetching receipts and activating coupons
### Receipts

Get your receipts as json and receive a list of bought items like:
```json
{
    "currentUnitPrice": "2,19",
    "quantity": "1",
    "isWeight": false,
    "originalAmount": "2,19",
    "name": "Vegane Frikadellen",
    "taxGroup": "1",
    "taxGroupName": "A",
    "codeInput": "4023456245134",
    "discounts": [
        {
            "description": "5€ Coupon",
            "amount": "0,21"
        }
    ],
    "deposit": null,
    "giftSerialNumber": null
},
```

#### Commandline-Tool
```bash
$ lidl-plus --language=de --country=AT --refresh-token=XXXXX receipt --all > data.json
```

#### Python
```python
from lidlplus import LidlPlusApi

lidl = LidlPlusApi("de", "AT", refresh_token="XXXXXXXXXX")
for receipt in lidl.tickets():
    pprint(lidl.ticket(receipt["id"]))
```

### Coupons

You can list all coupons and activate/deactivate them by id.
The coupons API now returns `sections` containing `promotions` with a `validity` range:
```json
{
    "sections": [
        {
            "name": "AllStores",
            "promotions": [
                {
                    "id": "2c9b3554-a09c-412c-8be4-d41cbff13572",
                    "image": "https://lidlplusprod.blob.core.windows.net/images/coupons/LT/IDISC0000254911.png?t=1695452076",
                    "type": "Standard",
                    "offerTitle": "1 + 1",
                    "title": "👨🏻‍🍳 Frozen 👨🏻‍🍳",
                    "offerDescriptionShort": "FREE",
                    "validity": {
                        "start": "2023-09-24T21:00:00Z",
                        "end": "2023-10-01T20:59:59Z"
                    },
                    "isActivated": false,
                    "promotionId": "DISC0000254911",
                    "isSpecial": false,
                    "isHappyHour": false,
                    "stores": []
                },
                .......
            ]
        }
    ]
}
```

#### Commandline-Tool

Activate all available coupons

```bash
$ lidl-plus --language=de --country=AT --refresh-token=XXXXX coupon --all
```

#### Python
```python
from lidlplus import LidlPlusApi

lidl = LidlPlusApi("de", "AT", refresh_token="XXXXXXXXXX")
for section in lidl.coupons()["sections"]:
  for coupon in section["promotions"]:
    print("found coupon: ", coupon["title"], coupon["id"])
```

## Help
#### Commandline-Tool
```commandline
Lidl Plus API

options:
  -h, --help                show this help message and exit
  -c CC, --country CC       country (DE, BE, NL, AT, ...)
  -l LANG, --language LANG  language (de, en, fr, it, ...)
  -u USER, --user USER      Lidl Plus login username
  -p XXX, --password XXX    Lidl Plus login password
  --2fa {phone,email}       choose two factor auth method
  -m {email,phone}, --method {email,phone}
                            login with email address or phone number
  -r TOKEN, --refresh-token TOKEN
                            refresh token to authenticate
  --skip-verify             skip ssl verification
  --not-accept-legal-terms  not auto accept legal terms updates
  -d, --debug               debug mode

commands:
  auth                      authenticate and get token
  receipt                   output last receipts as json
  coupon                    activate coupons
```

## Support
If you find this project helpful and would like to support its development, you can buy me a coffee! ☕

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/andre0512)

Don't forget to star the repository if you found it useful! ⭐
