# The public site Intuit asks for

Intuit will not issue production QuickBooks credentials without a public licence agreement, a public privacy policy, and a set of customer-facing URLs — even for an application that has no customers and no web interface, as this one does not. These five pages are the smallest honest answer to that: they say what the agent is, how it is connected and disconnected, and what it does with information.

| File | What it is for |
|---|---|
| `index.html` | The launch URL. Says plainly that this is internal software with nothing to sign in to. |
| `connect.html` | The connect/reconnect URL. Describes `fops qbo-connect`. |
| `disconnect.html` | The disconnect URL. How to end the connection from either side. |
| `eula.html` | The end-user licence agreement. |
| `privacy.html` | The privacy policy. The substantive one. |

## What goes in the Intuit app profile

With the site at `https://<account>.github.io/icon-legal/`:

- **Host domain:** `<account>.github.io` (no protocol, as the field says)
- **Launch URL:** `https://<account>.github.io/icon-legal/index.html`
- **Disconnect URL:** `https://<account>.github.io/icon-legal/disconnect.html`
- **Connect/Reconnect URL:** `https://<account>.github.io/icon-legal/connect.html`
- **EULA URL:** `https://<account>.github.io/icon-legal/eula.html`
- **Privacy policy URL:** `https://<account>.github.io/icon-legal/privacy.html`

On Icon's own domain instead, the host domain is `icon-technologies.com` and the six URLs follow the same shape. Write whichever set is live into `docs/open-questions.md`.

## Hosting

Anywhere that serves them publicly over https. A small **public** GitHub repository with Pages turned on is the quickest: nothing in these pages is sensitive. Do not put them in this repository's own hosting — this repository is private and holds real settings.

## Keeping them true

These pages describe real behaviour: what the agent reads, who it emails, what goes to Anthropic and to Intuit, where backups live and how long they are kept, and how connecting works. A change to any of that changes these pages too, in the same PR. A privacy policy that is out of date is worse than none, because it is a public statement that is no longer true.

The address, the governing law (Georgia), and the contact address (`kevin@icon-technologies.com`) are filled in. What is left is for someone to read them against Icon's client contracts: the privacy policy says timesheet documents are sent to Anthropic to be read, which is true and has to be there, but if a client contract limits where that client's data may be processed, that is worth knowing before the policy is public rather than after.
