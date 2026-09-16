# The two pages Intuit asks for

Intuit will not issue production QuickBooks credentials without a public end-user licence agreement and a public privacy policy, even for an app only Icon connects to its own company. `eula.html` and `privacy.html` are those two pages. They live here so a change to them is reviewed like any other change, and so they stay true to what the agent actually does.

## Before publishing them

Both pages are filled in: Icon's address at 5755 North Point Pkwy, Suite 28, Alpharetta, GA 30022, Georgia law in section 10 of the EULA, and `kevin@icon-technologies.com` as the address a consultant or client contact writes to.

What is left is to have someone read them against Icon's client contracts. The privacy policy says that timesheet documents are sent to Anthropic to be read. That is true and it has to be in there, but if a client contract limits where that client's data may be processed, that is worth knowing before the policy is public rather than after.

## Where they go

Anywhere that serves them publicly over https. Two options:

- **On Icon's own domain**, for example `https://icon-technologies.com/eula` and `/privacy`. Best, if there is a site.
- **GitHub Pages**, from a small *public* repository containing nothing but these two files. That gives `https://<account>.github.io/<repo>/eula.html` and `/privacy.html`. Nothing in either page is sensitive, so a public repository costs nothing.

Whichever it is, paste the two addresses into the Intuit developer portal, and write them down in `docs/open-questions.md` so the next person knows where they are.

## Keeping them true

These pages describe real behaviour: what the agent reads, who it emails, what goes to Anthropic and to Intuit, where backups live and how long they are kept. A change to any of that changes these pages too, in the same PR — an out-of-date privacy policy is worse than none, because it is a public statement that is no longer true.
