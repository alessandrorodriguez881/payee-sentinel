# Hallberg Components GmbH: vendor payment policy (invented for the demo)

## Bank-change requests
A request to change a vendor's bank details is confirmed by calling the vendor on the phone number that was on file at
least 60 days before the request. Never call a number given in the request itself, and never a number that was
changed recently, because an attacker who controls the vendor's account can change the phone first.

## Blocking a change
A confirmed callback is not enough when other channels show the vendor's account being taken over. The vendor-master
agent blocks a bank change when the vendor's risk signals from all channels add up to 6 or more, and a person reviews it.

## Payments to new bank details
The first payment to bank details changed in the last 30 days gets a step-up call to the number on file 60 days ago
when the vendor's risk adds up to 4 or more. At 7 or more the payment is held for a person.

## After a confirmed attack
The vendor's real bank details are restored and frozen until a person clears them. The case is written up, and the
person decides whether its pattern becomes collective memory for every agent.
