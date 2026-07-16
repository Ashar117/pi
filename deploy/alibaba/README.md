# Deploying Pi on Alibaba Cloud (ECS + Model Studio)

Pi's backend runs on an Alibaba Cloud ECS instance; all LLM calls go to Qwen via
Alibaba Cloud Model Studio (DashScope). The provider integration lives in
[`core/providers/qwen.py`](../../core/providers/qwen.py) — the DashScope
OpenAI-compatible endpoint (`https://dashscope-intl.aliyuncs.com/compatible-mode/v1`).

## 1. Get a Qwen API key

1. Sign up for [Alibaba Cloud Model Studio](https://bit.ly/qwencloud-getapi).
2. Create an API key and note it — it becomes `QWEN_API_KEY` below.

## 2. Provision ECS

- Smallest Ubuntu 22.04+ instance is enough (Pi's backend is I/O-bound, not CPU-bound).
- Security group: allow inbound TCP on your chosen HTTP port (default `7712`) from
  your IP, plus SSH (22).

## 3. Install and configure

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git
git clone https://github.com/Ashar117/pi.git && cd pi
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cat > .env <<'EOF'
QWEN_API_KEY=sk-your-dashscope-key
QWEN_MODEL=qwen-max
# Server exposure — token is MANDATORY when binding beyond localhost;
# the server refuses to start without it.
PI_SERVER_HOST=0.0.0.0
PI_SERVER_TOKEN=generate-a-long-random-string
PI_HTTP_PORT=7712
# T-300: composite salience (recency/surprise/affect-aware retrieval) — run the
# full forgetting/ranking stack on the deploy box. Decay-archive is on by default.
PI_SALIENCE_MODE=composite
# Optional: Supabase for L1/L2 memory tiers (L3 runs on local SQLite without it)
# SUPABASE_URL=...
# SUPABASE_KEY=...
EOF
```

## 4. Run under systemd

```bash
sudo tee /etc/systemd/system/pi.service <<EOF
[Unit]
Description=Pi agent daemon
After=network.target

[Service]
WorkingDirectory=/home/$(whoami)/pi
ExecStart=/home/$(whoami)/pi/venv/bin/python pi_daemon.py
Restart=on-failure
User=$(whoami)

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now pi
```

## 5. Verify

```bash
# health (no auth needed for /health when token unset locally; with token:)
curl -H "Authorization: Bearer $PI_SERVER_TOKEN" http://<ecs-public-ip>:7712/health

# real chat round-trip through Qwen
curl -X POST http://<ecs-public-ip>:7712/chat \
  -H "Authorization: Bearer $PI_SERVER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Remember: my favorite color is teal."}'
```

## Cost guardrails

Qwen usage is capped by a per-day token budget
(`QWEN_DAILY_TOKEN_BUDGET`, default 50,000 — see `PROVIDER_DAILY_TOKEN_BUDGET`
in [`core/llm_router.py`](../../core/llm_router.py)). At >90% utilization the
router browns Qwen out and falls back to the next provider in the tier.
