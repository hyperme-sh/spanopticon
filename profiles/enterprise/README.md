# Enterprise deployment configurations

Choose a site preset to replicate its web app, or start with the generic Spanopticon template and select the services you operate.

| Configuration | Starts by default | Suggested additions |
| --- | --- | --- |
| s3r.ch | s3r.ch web app with persistent Gun and forum data | Use Auth and User for shared sign-in, or Edge with ACL and Storage for the Spanopticon integrations your site uses. Add Payment for paid features or leases. Add Verification when a product flow uses it. |
| Hyperme.sh | Hyperme.sh web app | Add Portal, Edge, Auth and User for an account console. Add Gateway and Plane for a compute marketplace, and Payment for billing or payouts. Add Verification when a product flow uses it. |
| Spanopticon | Select the services you need | Enable Portal, Edge, Auth, User, ACL, Storage, Gateway, Plane, Payment or Verification individually. |

The site presets work independently of Payment and Verification. For either optional
service, follow its owner deployment instructions for databases, migrations,
workers and credentials, or connect to an existing instance. The generic template
starts the selected service containers. Configure their upstream endpoints, databases
and any external identity provider for your environment.

- [s3r.ch minimal install](https://github.com/FyberLabs/s3r.ch/blob/master/docs/install-minimal.md)
- [s3r.ch Spanopticon integration](https://github.com/hyperme-sh/spanopticon/blob/main/docs/s3rch-interfaces.md)
- [Payment service](https://github.com/hyperme-sh/payment-service)
- [Verification deployment](https://github.com/hyperme-sh/spanopticon/tree/main/verification)

## Compose

Run these commands from this directory. Set `SPANOPTICON_CONFIG_DIR` to the
directory containing your service environment files. The s3r.ch preset reads
`s3rch-web.env`. Give it an `IDENTITY_SESSION_SECRET` and supply the identity
storage configuration if your site supports sign-in. Gun and forum data stay
in the `s3rch-data` volume.

```sh
docker compose -p s3rch -f compose.s3rch.yaml up -d
docker compose -p hyperme -f compose.hyperme.yaml up -d
docker compose -p spanopticon -f compose.spanopticon.yaml --profile portal up -d
```

The site presets bind to loopback. If both run on one machine, set
`HYPERME_WEB_PORT` or `S3RCH_WEB_PORT` to a different host port. Connect them to
your HTTPS ingress. The Hyperme.sh default image comes from its owner's private
registry. Set `HYPERME_WEB_IMAGE` to the tagged image in a registry you can pull
from when running your own copy. Each generic service reads a matching
`<service>.env`. Enable additional services with another `--profile` flag. To add one to a
site release, pass the generic file alongside the site file. For example:

```sh
docker compose -p s3rch -f compose.s3rch.yaml -f compose.spanopticon.yaml --profile storage up -d
```

## Helm

Use the same chart with one of the three named values files. Create the ConfigMaps
and Secrets referenced by the enabled services in the release namespace. Set
`imagePullSecrets` for private images. Set `services.hyperme-web.image` to your
registry's tagged image if you run a copy of the Hyperme.sh web app. For s3r.ch, supply a persistent claim named
`s3rch-data` or set `services.s3rch-web.data.existingClaim` to your claim. It runs one
writer and replaces that pod before starting its successor.

```sh
helm upgrade --install s3rch ./helm -f helm/values.s3rch.yaml -n s3rch
helm upgrade --install hyperme ./helm -f helm/values.hyperme.yaml -n hyperme
helm upgrade --install spanopticon ./helm -f helm/values.spanopticon.yaml -n spanopticon \
  --set services.portal.enabled=true
```

Add `-f helm/values.spanopticon.yaml` before a site values file to let that release
select optional services too. The chart creates internal Services. Point your ingress
at the appropriate site Service. Set the image tag for each enabled service
independently. Compose pulls the selected tags on startup. Each Helm upgrade
replaces the pods so Kubernetes pulls those tags again.

## Download a TEST bundle

Each preset has its own GHCR package. Pull the moving `test` tag to get the
latest published configuration, or substitute its published `0.24.0-test.N`
tag to retrieve a specific version.

```sh
oras pull ghcr.io/hyperme-sh/spanopticon-config-s3rch:test
oras pull ghcr.io/hyperme-sh/spanopticon-config-hyperme:test
oras pull ghcr.io/hyperme-sh/spanopticon-config-spanopticon:test
```

Extract the downloaded preset archive and run the matching Compose or Helm
command above. The separate `bundle.json` records the exact configuration tag
and the service image tags the files use. The bundle and service tags move
independently. Today the presets follow the latest image on each configured TEST
channel. After the v0.24 development line, the presets will recommend a specific
major-version channel for each service.
