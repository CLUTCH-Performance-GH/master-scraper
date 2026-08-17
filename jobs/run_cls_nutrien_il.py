"""CLS project: Nutrien Ag Solutions ILLINOIS contacts (IL not in the original 23
target states). Writes to output/contacts/nutrien_il.json — does NOT touch the
ag-roster nutrien.json. Discovery + Places phones + current-employment verify.
"""
import jobs.contacts_lib as cl

# enable Illinois as a valid target just for this run
cl.STATE_ABBR["Illinois"] = "IL"
cl.ABBR_STATE["IL"] = "Illinois"
cl.TARGET_ABBR.add("IL")
if "Illinois" not in cl.TARGET_STATES:
    cl.TARGET_STATES.append("Illinois")

import jobs.run_company as rc
import jobs.verify_current as vc

# clone the Nutrien config under a new key so output lands in nutrien_il.json
rc.COMPANIES["nutrien_il"] = dict(rc.COMPANIES["nutrien"])
rc.VERIFIED_PATTERNS["nutrien_il"] = "first.last"

if __name__ == "__main__":
    rc.run_company("nutrien_il", ["Illinois"], corroborate=True)
    vc.verify("nutrien_il")
    print("=== Nutrien IL contacts done -> output/contacts/nutrien_il.json ===")
