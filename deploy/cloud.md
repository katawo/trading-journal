# Trade Compass — New Oracle Cloud VM & Docker Deployment Guide

This is the complete procedure we followed, consolidated into a reusable deployment runbook.

It covers:

```text
Oracle Cloud VM
    ↓
Ubuntu x86_64
    ↓
Public networking
    ↓
SSH + GitHub deploy key
    ↓
Swap
    ↓
Docker + Compose
    ↓
Trade Compass
    ├── Caddy
    ├── Streamlit Web
    └── Ingestion API
```

MT5/Wine is intentionally a **second deployment stage** after the web application is stable.

---

## 1. Target architecture

The server architecture is:

```text
                         INTERNET
                            │
                       80 / 443
                            │
                            ▼
                    Oracle Cloud VM
                  Ubuntu 24.04 x86_64
                            │
              ┌─────────────┴─────────────┐
              │                           │
           Docker                    Host services
              │                           │
       ┌──────┼──────┐              Future stage
       │      │      │                   │
     Caddy   Web  Ingestion          Xvfb + Wine
       │      │      │                   │
       │   :8501   :8600                MT5
       │                                  │
       └──── public gateway          Investor login
```

Only Caddy should be publicly exposed.

Do **not** publicly expose:

```text
8501  Streamlit
8600  Ingestion API
```

---

# Part I — Oracle Cloud

## 2. Create the VCN

In OCI:

**Networking → Virtual Cloud Networks → Create VCN**

Use:

```text
Name:       trade-compass-vcn
IPv4 CIDR:  10.0.0.0/16
```

Create it.

---

## 3. Create the public subnet

Inside:

**trade-compass-vcn → Subnets → Create Subnet**

Use:

```text
Name:
trade-compass-public-subnet

Subnet type:
Regional

IPv4 CIDR:
10.0.1.0/24

Subnet access:
Public Subnet

DNS:
Enabled

DHCP:
Default DHCP Options

Security:
Default Security List
```

Make sure OCI is **not prohibiting public IPv4 addresses** on the subnet.

---

## 4. Create Internet Gateway

Go to:

**trade-compass-vcn → Gateways → Internet Gateway**

Create:

```text
Name:
trade-compass-gw
```

Enable it.

---

## 5. Configure routing

Go to:

**trade-compass-vcn → Routing → Default Route Table**

Add:

```text
Target Type:
Internet Gateway

Destination Type:
CIDR Block

Destination:
0.0.0.0/0

Target:
trade-compass-gw
```

The resulting network is:

```text
VM
 │
10.0.1.x
 │
Public subnet
 │
0.0.0.0/0
 │
trade-compass-gw
 │
Internet
```

---

# Part II — Firewall

## 6. Configure OCI Security List

Go to:

**VCN → Security → Default Security List**

You need TCP ingress for:

```text
22   SSH
80   HTTP
443  HTTPS
```

Add:

### HTTP

```text
Source CIDR:       0.0.0.0/0
Protocol:          TCP
Destination Port:  80
Description:       Caddy HTTP
```

### HTTPS

```text
Source CIDR:       0.0.0.0/0
Protocol:          TCP
Destination Port:  443
Description:       Caddy HTTPS
```

SSH `22` will normally already exist.

For initial installation:

```text
0.0.0.0/0 → TCP 22
```

works, but later it is better to restrict SSH to your own IP.

Do **not** create public rules for:

```text
8501
8600
```

---

# Part III — Create the VM

## 7. Recommended VM

For the future MT5/Wine architecture, prefer **x86_64**.

The VM we ended up using was:

```text
Shape:
VM.Standard.E2.1.Micro

Architecture:
x86_64

CPU:
1 OCPU

RAM:
~1 GB
```

It is constrained, but usable for initial deployment with swap.

For a production Trade Compass + MT5 machine, I'd prefer:

```text
2+ vCPU
4 GB RAM minimum
8 GB preferred
```

---

## 8. Ubuntu image

For x86 Oracle shapes use a current Ubuntu LTS x86_64 image.

Prefer:

```text
Ubuntu 24.04 LTS
x86_64
```

Do **not** select an `aarch64` image for an x86 E2 instance.

Likewise, if using Oracle A1/Ampere:

```text
A1 → ARM64/aarch64
```

But A1 is **not my preferred target for the future MT5/Wine server**, because MT5 is a Windows x86/x64 application.

---

## 9. VM networking

During VM creation select:

```text
VCN:
trade-compass-vcn

Subnet:
trade-compass-public-subnet

Public IPv4:
YES
```

A public IPv4 is required for this deployment unless you're introducing another ingress layer.

---

# Part IV — SSH

## 10. Save the Oracle SSH private key

On your laptop, for example:

```text
~/.ssh/trade-compass-ssh-key-2026-08-17.key
```

Protect it:

```bash
chmod 600 ~/.ssh/trade-compass-ssh-key-2026-08-17.key
```

---

## 11. Connect

For our current VM:

```bash
ssh -o IdentitiesOnly=yes \
  -i ~/.ssh/trade-compass-ssh-key-2026-08-17.key \
  ubuntu@168.107.93.149
```

`IdentitiesOnly=yes` is important if your SSH agent contains many keys.

Without it, you encountered:

```text
Too many authentication failures
```

because SSH was offering too many identities.

---

## 12. Optional SSH alias

On your laptop:

```bash
nano ~/.ssh/config
```

Add:

```text
Host trade-compass
    HostName 168.107.93.149
    User ubuntu
    IdentityFile ~/.ssh/trade-compass-ssh-key-2026-08-17.key
    IdentitiesOnly yes
```

Protect it:

```bash
chmod 600 ~/.ssh/config
```

Then simply:

```bash
ssh trade-compass
```

---

# Part V — Prepare Ubuntu

Everything below is run **on the Oracle VM**.

## 13. Inspect the machine

```bash
uname -m
free -h
df -h
cat /etc/os-release
```

For the x86 server:

```text
x86_64
```

is what we want.

---

## 14. Add swap

This is especially important for the 1 GB E2 Micro.

Create 2 GB:

```bash
sudo fallocate -l 2G /swapfile
```

Protect it:

```bash
sudo chmod 600 /swapfile
```

Format:

```bash
sudo mkswap /swapfile
```

Enable:

```bash
sudo swapon /swapfile
```

Persist across reboot:

```bash
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Verify:

```bash
free -h
```

Our resulting machine looked approximately like:

```text
Mem:   954 MiB
Swap:  2.0 GiB
```

That's good.

---

## 15. Update Ubuntu

```bash
sudo apt update
sudo apt upgrade -y
```

Then reboot:

```bash
sudo reboot
```

Reconnect:

```bash
ssh trade-compass
```

or with the full command.

---

# Part VI — Install basic tools

## 16. Install Make and Git

```bash
sudo apt update
sudo apt install -y \
  make \
  git \
  curl \
  ca-certificates
```

Verify:

```bash
make --version
git --version
```

`make` is required because your deployment command is:

```bash
make deploy-docker
```

---

# Part VII — Install Docker

## 17. Add Docker signing key

```bash
sudo install -m 0755 -d /etc/apt/keyrings
```

Then:

```bash
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
```

Set permissions:

```bash
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

---

## 18. Add Docker repository

```bash
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

Then:

```bash
sudo apt update
```

---

## 19. Install Docker

```bash
sudo apt install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin
```

Verify:

```bash
docker --version
docker compose version
```

---

## 20. Allow Ubuntu user to use Docker

```bash
sudo usermod -aG docker $USER
```

Log out:

```bash
exit
```

Reconnect:

```bash
ssh trade-compass
```

Test:

```bash
docker run --rm hello-world
```

Then:

```bash
docker ps
```

Neither should require `sudo`.

---

# Part VIII — GitHub private repository

Your repository is:

```text
git@github.com:katawo/trading-journal.git
```

Do **not** copy your laptop's private GitHub key to the server.

Create a dedicated deployment key.

---

## 21. Generate server GitHub key

On the Oracle VM:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
```

Generate:

```bash
ssh-keygen -t ed25519 \
  -C "trade-compass-oracle-server" \
  -f ~/.ssh/trade_compass_github
```

For unattended deployment, leave the passphrase empty.

---

## 22. Get public key

```bash
cat ~/.ssh/trade_compass_github.pub
```

Copy the complete line.

---

## 23. Add GitHub Deploy Key

In GitHub:

```text
katawo/trading-journal
        ↓
Settings
        ↓
Deploy keys
        ↓
Add deploy key
```

Use:

```text
Title:
Trade Compass Oracle Server

Key:
<public key>

Allow write access:
OFF
```

The server only needs:

```text
clone
fetch
pull
```

so read-only access is preferable.

---

## 24. Configure server SSH for GitHub

On Oracle:

```bash
nano ~/.ssh/config
```

Add:

```text
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/trade_compass_github
    IdentitiesOnly yes
```

Then:

```bash
chmod 600 ~/.ssh/config
chmod 600 ~/.ssh/trade_compass_github
chmod 644 ~/.ssh/trade_compass_github.pub
```

---

## 25. Trust GitHub and test

Run:

```bash
ssh -T git@github.com
```

When asked:

```text
Are you sure you want to continue connecting?
```

enter:

```text
yes
```

GitHub will be added to:

```text
~/.ssh/known_hosts
```

A successful authentication followed by GitHub saying it doesn't provide shell access is normal.

---

# Part IX — Clone Trade Compass

## 26. Production directory

I recommend:

```text
/opt/trade-compass
```

Create:

```bash
sudo mkdir -p /opt/trade-compass
sudo chown ubuntu:ubuntu /opt/trade-compass
```

Because the directory is empty:

```bash
git clone \
  git@github.com:katawo/trading-journal.git \
  /opt/trade-compass
```

Enter it:

```bash
cd /opt/trade-compass
```

Verify:

```bash
git status
git remote -v
```

You should have:

```text
origin  git@github.com:katawo/trading-journal.git
```

---

# Part X — Configure Trade Compass

## 27. Inspect deployment files

```bash
cd /opt/trade-compass
ls deploy
```

You should have your Docker deployment files, including:

```text
docker-compose.yml
.env.example
```

and the Docker Caddy configuration used by Compose.

---

## 28. Create deployment environment

```bash
cp deploy/.env.example deploy/.env
```

Generate a strong cookie secret:

```bash
openssl rand -hex 32
```

Copy the result.

Edit:

```bash
nano deploy/.env
```

Set at least:

```text
TRADING_JOURNAL_MULTIUSER_COOKIE_KEY=<your-random-secret>
```

Use the exact variable names expected by your current repository's `.env.example`.

Never commit:

```text
deploy/.env
```

to Git.

---

# Part XI — Configure Caddy

## 29. Docker vs host Caddy

This distinction is important.

A **host/systemd** Caddy configuration uses:

```caddy
reverse_proxy 127.0.0.1:8501
reverse_proxy 127.0.0.1:8600
```

A **Docker Caddy container** should use Compose service names:

```caddy
reverse_proxy web:8501
reverse_proxy ingestion:8600
```

because `127.0.0.1` inside the Caddy container means the Caddy container itself.

---

## 30. Initial IP-only Caddy configuration

Before you have a domain, use HTTP.

The Docker Caddy configuration should effectively be:

```caddy
:80 {
    handle /ingest* {
        reverse_proxy ingestion:8600
    }

    handle /health {
        reverse_proxy ingestion:8600
    }

    handle {
        reverse_proxy web:8501
    }
}
```

Check which Caddyfile Compose mounts:

```bash
grep -n -A20 'caddy:' deploy/docker-compose.yml
```

Edit that file.

For example:

```bash
nano deploy/Caddyfile.docker
```

if that's what Compose references.

---

# Part XII — Pre-deployment checks

## 31. Verify memory

```bash
free -h
```

You want your 2 GB swap active.

---

## 32. Verify ports

```bash
sudo ss -ltnp | grep -E ':80 |:443 '
```

Before Docker/Caddy starts, ideally this produces no output.

If Apache or nginx appears, stop/remove it before proceeding.

---

## 33. Verify Docker

```bash
docker --version
docker compose version
docker ps
```

---

## 34. Verify environment

```bash
test -f deploy/.env && echo "deploy/.env OK"
```

And:

```bash
grep -q '^TRADING_JOURNAL_MULTIUSER_COOKIE_KEY=.' deploy/.env \
  && echo "Cookie key configured"
```

Don't print the secret itself unnecessarily.

---

# Part XIII — Deploy

## 35. Build and start

From:

```bash
cd /opt/trade-compass
```

run:

```bash
make deploy-docker
```

Your Makefile effectively runs:

```bash
docker compose \
  -f deploy/docker-compose.yml \
  --env-file deploy/.env \
  up -d --build
```

On the 1 GB E2 Micro, the first Python image build may be slow and may use swap heavily. That's expected.

---

## 36. Check containers

```bash
docker compose \
  -f deploy/docker-compose.yml \
  --env-file deploy/.env \
  ps
```

You want approximately:

```text
web         Up
ingestion   Up
caddy       Up
```

---

## 37. Check memory after deployment

```bash
free -h
```

Also:

```bash
docker stats --no-stream
```

This is particularly important on the E2 Micro.

---

# Part XIV — Troubleshooting

## 38. Web logs

```bash
docker compose \
  -f deploy/docker-compose.yml \
  --env-file deploy/.env \
  logs --tail=100 web
```

## 39. Ingestion logs

```bash
docker compose \
  -f deploy/docker-compose.yml \
  --env-file deploy/.env \
  logs --tail=100 ingestion
```

## 40. Caddy logs

```bash
docker compose \
  -f deploy/docker-compose.yml \
  --env-file deploy/.env \
  logs --tail=100 caddy
```

Follow live logs with:

```bash
docker compose \
  -f deploy/docker-compose.yml \
  --env-file deploy/.env \
  logs -f
```

---

# Part XV — Test the application

## 41. Test from the VM

```bash
curl http://localhost/health
```

Then:

```bash
curl -I http://localhost
```

---

## 42. Test externally

From your laptop:

```bash
curl http://168.107.93.149/health
```

Then open:

```text
http://168.107.93.149
```

At this point traffic flows:

```text
Browser
   │
   │ :80
   ▼
Oracle Security List
   │
   ▼
Caddy container
   │
   ├── /          → web:8501
   │
   ├── /health    → ingestion:8600
   │
   └── /ingest*   → ingestion:8600
```

---

# Part XVI — Create the first Trade Compass user

## 43. Important: Docker user creation

For the Docker deployment, don't use:

```bash
make web-user USER_NAME=alice
```

That command is designed for your **systemd deployment path** and uses:

```text
/var/lib/trade-compass
```

on the host.

Instead run the user-management script inside the Docker environment:

```bash
docker compose \
  -f deploy/docker-compose.yml \
  --env-file deploy/.env \
  run --rm web \
  python scripts/add_web_user.py alice \
  --name "Alice" \
  --email alice@example.com
```

Enter the password when prompted.

The credentials are then written into the Docker deployment's persistent data volume.

---

# Part XVII — Normal deployment updates

Once the server is established, future releases are much simpler.

## 44. Pull new code

```bash
cd /opt/trade-compass
git status
git pull
```

Then redeploy:

```bash
make deploy-docker
```

Because Compose uses:

```text
up -d --build
```

it rebuilds changed images and recreates services as necessary.

Persistent Docker data remains preserved.

---

## 45. Stop deployment

```bash
make deploy-docker-down
```

Your Docker volume remains preserved according to your current deployment design.

Start again:

```bash
make deploy-docker
```

---

# Part XVIII — Add a real domain

Once the IP deployment works, point a DNS record such as:

```text
tradecompass.example.com
        ↓
168.107.93.149
```

Then change the Docker Caddyfile from:

```caddy
:80 {
```

to:

```caddy
tradecompass.example.com {
```

while retaining:

```caddy
handle /ingest* {
    reverse_proxy ingestion:8600
}

handle /health {
    reverse_proxy ingestion:8600
}

handle {
    reverse_proxy web:8501
}
```

Redeploy:

```bash
make deploy-docker
```

With ports `80` and `443` reachable, Caddy can manage HTTPS certificates.

Then users access:

```text
https://tradecompass.example.com
```

instead of the raw IP.

---

# Part XIX — Security hardening

Once everything works, I would make several improvements.

### Restrict SSH

Instead of:

```text
0.0.0.0/0 → TCP 22
```

restrict OCI's SSH ingress rule to your public IP where practical.

Keep:

```text
0.0.0.0/0 → 80
0.0.0.0/0 → 443
```

because those are public web ports.

### Never expose application ports

Don't create OCI rules for:

```text
8501
8600
```

### Protect secrets

Never commit:

```text
deploy/.env
private SSH keys
MT5 investor passwords
ingestion tokens
```

### Keep the GitHub deploy key read-only

The Oracle server normally needs to **pull code**, not push code.

---

# Part XX — MT5 comes afterward

Once Docker Trade Compass is stable, we'll implement the second layer:

```text
HOST
├── Xvfb
├── Wine
├── dedicated minimal MT5
│   └── investor login
└── MT5 connector
         │
         ▼
     localhost
         │
         ▼
Docker ingestion
```

The intended final architecture is:

```text
                       Broker
                         │
                         ▼
                   MT5 / Wine
                 Investor Access
                         │
                         ▼
                  Host Connector
                         │
                         ▼
              Trade Compass Ingestion
                         │
                         ▼
                Immutable MT5 Data
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
       Psychology      Risk       Trading System
            │            │            │
            └────────────┼────────────┘
                         ▼
                Review → Monitor → Improve
```

I would **not install Wine/MT5 until the Docker deployment is confirmed healthy**, especially on the current ~1 GB VM. At that point we should measure `docker stats` and decide whether this E2 Micro has enough headroom for MT5 or whether MT5 needs a larger x86 VM.
