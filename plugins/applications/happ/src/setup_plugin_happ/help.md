# Happ

## 🔐 Subscriptions

Save the subscription name and source format. Select **system-keyring** to retain a secret reference. Select **enter-in-happ** when a keyring is unavailable or you prefer to supply the URL on each import. In this mode, enter the URL only in Happ’s native subscription dialog. setupSystem stores the local name, source format and per-subscription routing choices. Use the subscription's Import action to open the instructions for the native Happ dialog. URL tokens can occur in the path or query. The manager does not launch a VPN connection. [Happ configuration imports](https://www.happ.su/main/dev-docs/examples-of-links-and-parameters).

## ⚙️ Routing

Choose a standard subscription and profile name, then add domain or IP network rules for direct, proxy or block. The export contains a documented routing link. Import it into the selected subscription and verify the name in Happ. An existing native profile with the same name can be overwritten. JSON subscriptions receive routing from their provider. Their manual routing editor is disabled. A subscription removal in Happ also removes the routes bound to it. [Happ routing](https://www.happ.su/main/dev-docs/routing).

## ⚠️ Applying changes

A reconnect activates pending changes and may interrupt remote access. Check the selected server and route in Happ before reconnecting.

## ⚙️ Rule lists and DNS

Select First or Last to place a domain or network within its direct, proxy or block list. An optional domestic UDP resolver sets the documented DomesticDNSType and DomesticDNSIP fields on a standard subscription’s exported route. JSON subscriptions keep the provider’s rules and tunnel DNS controls in Happ. [Routing profile fields](https://www.happ.su/main/dev-docs/routing).
