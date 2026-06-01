# PortalCalc License Server

This is a small FastAPI license API for local testing and simple hosted use.

## Install

```powershell
python -m pip install fastapi uvicorn cryptography
```

## Generate signing keys

```powershell
python license_server.py keygen
```

Keep `PORTALCALC_LICENSE_PRIVATE_KEY` on the server only. Put
`PORTALCALC_LICENSE_PUBLIC_KEY` in the desktop app environment so `licensing.py`
can verify signed tokens.

## Create the database and a license key

```powershell
$env:PORTALCALC_LICENSE_DB = "portalcalc_licenses.db"
python license_server.py init-db
python license_server.py create-license --customer "Example Customer" --plan "standard" --max-machines 1
```

## Run locally

```powershell
$env:PORTALCALC_LICENSE_PRIVATE_KEY = "<server private key hex>"
$env:PORTALCALC_LICENSE_ADMIN_TOKEN = "<random admin token>"
uvicorn license_server:app --host 127.0.0.1 --port 8000
```

Then configure the app:

```powershell
$env:PORTALCALC_LICENSE_ENFORCED = "1"
$env:PORTALCALC_LICENSE_API = "http://127.0.0.1:8000"
$env:PORTALCALC_LICENSE_PUBLIC_KEY = "<public key hex>"
python main.py
```

## API

- `GET /health`
- `GET /public-key`
- `POST /activate` with `license_key` and `machine_id`
- `POST /check` with `token` and `machine_id`
- `POST /admin/licenses` with `X-Admin-Token`, `customer`, `plan`, optional `expires_at`, and `max_machines`
- `GET /admin/licenses` with `X-Admin-Token` to list issued licenses
- `GET /admin/licenses/{license_key_hash}` with `X-Admin-Token` to view activations
- `POST /admin/licenses/{license_key_hash}/cancel` with `X-Admin-Token` to cancel a license
- `POST /admin/licenses/{license_key_hash}/reactivate` with `X-Admin-Token` to reactivate a canceled license
- `DELETE /admin/licenses/{license_key_hash}/activations/{machine_id}` with `X-Admin-Token` to remove one machine activation

The desktop app currently uses `/activate` and `/check`.

## Admin view

Open `/admin` on the hosted service, for example:

```text
https://your-service-name.onrender.com/admin
```

Enter the same admin token used for `PORTALCALC_LICENSE_ADMIN_TOKEN`. From
there you can create licenses, list current licenses, cancel/reactivate them,
and remove an activated machine so a customer can activate a replacement
machine.

## Render deployment

Create a **Web Service** on Render.

Use these settings:

- Runtime: `Python 3`
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn license_server:app --host 0.0.0.0 --port $PORT`
- Health Check Path: `/health`

Environment variables:

- `PORTALCALC_LICENSE_PRIVATE_KEY`: the server private key from `python license_server.py keygen`
- `PORTALCALC_LICENSE_ADMIN_TOKEN`: a long random admin password
- `PORTALCALC_LICENSE_DB`: a path on the persistent disk, for example `/var/data/portalcalc_licenses.db`
- `PORTALCALC_LICENSE_TOKEN_DAYS`: `30`

For production, keep the SQLite file on a Render persistent disk. Render's
normal filesystem is temporary, so a plain SQLite file outside the mounted disk
can be lost on restart/redeploy.

After deploy, the desktop app should use:

```powershell
$env:PORTALCALC_LICENSE_API = "https://your-service-name.onrender.com"
$env:PORTALCALC_LICENSE_PUBLIC_KEY = "<public key hex>"
```
