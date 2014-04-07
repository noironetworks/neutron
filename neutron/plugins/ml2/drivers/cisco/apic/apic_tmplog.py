from __future__ import print_function

logfile = open("/tmp/apic.tmplog", "w")
def apic_tmplog(*p):
    print(", ".join(map(str, p)) + "\n\n\n", file=logfile)
    logfile.flush()
