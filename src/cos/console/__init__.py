"""The operator console.

A local, read-mostly view of one run — who delegated to whom, what it cost, what is
waiting for a decision — plus the one button that authorises a send.

The console deliberately does **not** hold the authority to send anything. Its approve
button shells out to the operator's own `gh` credential to approve the protected `send`
environment, exactly as clicking the button on github.com would. Moving the interface is
safe. Moving the gate into the agent's reach would not be, so the gate stays where it is:
the Azure OIDC token that permits a send is only issued by GitHub *after* a human
approval, and nothing in this package can mint one.
"""
