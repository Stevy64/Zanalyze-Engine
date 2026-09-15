# Zanalyze Engine sur Oracle Cloud Always Free

À utiliser si **GitHub Actions** est bloqué par SofaScore (HTTP 403) ou si tu veux une API 24/7.

Le Free Tier Ampere (ARM, `VM.Standard.A1.Flex`) a un **egress libre** : SofaScore fonctionne, contrairement à PythonAnywhere.

## Instance

1. Compute → Ubuntu ARM (ex. 2 OCPU / 12 Go) + IP publique.
2. Security List : **22**, **8001** (API) — ou **80/443** derrière nginx.
3. Sur la VM :

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git
git clone https://github.com/Stevy64/Zanalyze-Engine.git
cd zanalyze-engine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# ENGINE_TOKEN=un-secret-long
```

## systemd — API

`/etc/systemd/system/zanalyze-engine.service` :

```ini
[Unit]
Description=Zanalyze Engine API
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/zanalyze-engine
EnvironmentFile=/home/ubuntu/zanalyze-engine/.env
ExecStart=/home/ubuntu/zanalyze-engine/.venv/bin/python -m engine serve --host 0.0.0.0 --port 8001
Restart=always

[Install]
WantedBy=multi-user.target
```

## systemd — worker (snapshot toutes les 2 h)

`/etc/systemd/system/zanalyze-engine-worker.service` :

```ini
[Unit]
Description=Zanalyze Engine worker
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/zanalyze-engine
EnvironmentFile=/home/ubuntu/zanalyze-engine/.env
ExecStart=/home/ubuntu/zanalyze-engine/.venv/bin/python -m engine refresh --pages 1 --passes 1
# Relancer via timer plutôt qu’une boucle : voir .timer
```

Timer `/etc/systemd/system/zanalyze-engine-worker.timer` :

```ini
[Unit]
Description=Refresh Zanalyze Engine every 2h

[Timer]
OnBootSec=2min
OnUnitActiveSec=2h
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now zanalyze-engine.service
sudo systemctl enable --now zanalyze-engine-worker.timer
```

Pour alimenter ZanalyZe sur PA **sans** que PA appelle l’API Oracle (whitelist) : après `refresh`, commit/push `exports/matchs.json` vers GitHub (deploy key) **ou** copie le JSON dans le repo ZanalyZe.

Docker sur la VM :

```bash
docker build -t zanalyze-engine .
docker run -d --restart unless-stopped -p 8001:8001 --env-file .env --name zanalyze-engine zanalyze-engine
docker run -d --restart unless-stopped --env-file .env --name zanalyze-engine-worker zanalyze-engine sh /app/deploy/worker-loop.sh
```

Les deux git (engine + PWA) peuvent cohabiter sur la même VM : nginx route `/` vers Django et `/engine/` vers le port 8001.
