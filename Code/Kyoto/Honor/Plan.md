# The "Honor" service
The Honor service is a service responsible for generating notifications honoring the victims of historic tragadies, via the use of the notifications system of the Yui shell.

## How it works
In most cases, the Honor service should appear to do nothing, this is expected.

While in the background, the service will check if today's date matches any dates specified.

Dates are specified somewhat like this (but not exactly, ofc):
```plaintext
Date: 09-11
Appear At: 08:46 AM EST, 09:03 AM EST, 09:37 AM EST, 10:03 AM EST
Title: Never Forget
Description: Honor those lost in the September 11 attacks, {year} ago.
When Clicked Type: Open URL In Default Browser
When Clicked Target: https://en.wikipedia.org/wiki/September_11_attacks
```

Depending on the state of the RTC at the time of the check:
* If this is the first check (e.g a system startup):
    * If the date 