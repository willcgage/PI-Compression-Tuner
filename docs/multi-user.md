# Shared server for several admins

The tuner can run on one Windows machine and serve its web UI to several people. Each person signs in with their Windows account and works in their own session. Settings (PI servers, the search limit, point templates and who may sign in) are shared.

## Set it up

Pick one:

- **Installer.** Run `PI-Compression-Tuner-<version>-Setup.exe` (on the Releases page, next to the zip) and tick **Run the tuner as a Windows service**. It asks for the port, the service account and the first admins. Silent install:

  ```
  PI-Compression-Tuner-<version>-Setup.exe /VERYSILENT /TASKS="service,firewall" /PORT=8501 /ADMINS="CORP\PI Admins"
  ```

- **Offline bundle.** Unzip it, then right-click **Set up shared server.cmd** and choose **Run as administrator**. Or, from an administrator command prompt in the bundle folder:

  ```
  pict service install --port 8501 --admins "CORP\PI Admins" --open-firewall
  ```

- **No service.** `pict serve` runs the same server in a console window until you close it.

People then open `http://SERVER:8501/` in Edge or Chrome. Both sign in with the Windows account without asking when the server is in the browser's Local intranet zone. Otherwise they are asked for a user name and password. Firefox needs the server added to `network.negotiate-auth.trusted-uris` in `about:config`.

## Who may sign in

On the Admin page, under **Who may sign in**, there are two lists. Each entry is a Windows account or group, written `DOMAIN\name`. A group covers everyone in it, including nested groups, as Windows decides.

| List | What its people can do |
|---|---|
| Admins | Use the tuner and change every setting, including these lists |
| Users | Use the tuner. They don't see the Admin page. |

Anyone in neither list sees a "No access" page. That visit is written to the activity log.

When the server is first set up, the admins list is `BUILTIN\Administrators`, which is the server's local Administrators group. `--admins` (or the installer's Admins box) replaces that list. The page won't save a change that removes your own admin rights. A change applies the next time someone signs in. Sign-ins last 8 hours, and a restart signs everyone out. On a domain machine, signing in again happens without a prompt.

The lists are saved in `[access]` in the settings file:

```toml
[access]
admins = ["BUILTIN\\Administrators", "CORP\\PI Admins"]
users = ["CORP\\PI Engineers"]
```

## Reading PI

PI is read as the account the server runs as, not as each person. Give that account a read-only PI mapping (PI identity with read access to points and data), and nothing more. The tuner never writes to PI.

- **NT AUTHORITY\NetworkService** (the default) reads as the server's computer account, `DOMAIN\SERVER$`. Map that account in PI (for PI Web API, the Web API sees the same account).
- **A group managed service account** (`CORP\svc-pict$`) needs no password.
- **A named domain account** needs its password. The installer asks for it. On the command line, set `PICT_SERVICE_PASSWORD` or answer the prompt. It is given **Log on as a service**. Its password is not stored in any file.

With a domain account (named or gMSA), Kerberos sign-in to the tuner needs the web address registered to that account. A domain admin runs this once:

```
setspn -S HTTP/SERVER CORP\svc-pict
setspn -S HTTP/server.corp.example.com CORP\svc-pict
```

With NetworkService, nothing needs to be registered.

## HTTPS

Windows sign-in never sends passwords. Without HTTPS, the pages, point data and sign-in cookie still cross the network unencrypted. To serve HTTPS, give a certificate and key in PEM format:

```
pict service install --cert C:\certs\pict.pem --key C:\certs\pict.key
```

The cookie is then marked Secure, and people browse to `https://SERVER:8501/`.

## Where things are

| What | Where |
|---|---|
| Settings and access lists | `C:\ProgramData\PI Compression Tuner\pict.toml` |
| Activity log (sign-ins, turned-away visits, saved settings) | `C:\ProgramData\PI Compression Tuner\pict-activity.log`, and on the Admin page |
| Service logs | `C:\ProgramData\PI Compression Tuner\logs` |
| Service | **PI Compression Tuner** (`PICompressionTuner`) in Services, set to start with Windows and restart on failure |

Setup locks the data folder so that only administrators and the service account can change it, and the program folder so that only administrators can. Ordinary users can't edit who may sign in or the code the service runs. If the setting-up admin has their own `pict.toml`, the service starts from a copy of it.

## Managing the service

From an administrator prompt in the program folder:

```
pict service status          # Started, Stopped or NonExistent
pict service stop
pict service start
pict service install ...     # again, to change the port, account or certificate; settings are kept
pict service uninstall       # settings and logs are kept
```

Uninstalling from Apps & features also removes the service.

## How sign-in works

`pict serve` runs the Streamlit UI in-process under uvicorn, behind a small sign-in gate (`serve.py`). The gate answers the browser's first request with an HTTP Negotiate challenge and checks the reply through Windows SSPI (pywin32). It reads the account name and checks the access lists against the account's Windows token. Then it sets a signed, HttpOnly cookie, so later requests and the UI's live WebSocket connection don't sign in again. It passes the person's name and role to the UI in headers. Any such headers a browser sends are removed first. Nothing reaches the UI except through the gate.

The service host is [WinSW](https://github.com/winsw/winsw) 2.12 (MIT license), shipped as `service\pict-service.exe`.
