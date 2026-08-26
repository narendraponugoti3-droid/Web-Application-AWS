#!/bin/bash
# EC2 launch template user data. Runs as root on first boot of an Amazon Linux 2023
# instance. Installs Java, pulls the application JAR from S3, resolves database
# credentials from Secrets Manager, and starts the app under systemd.
#
# Replace the five values in the CONFIG block before pasting into the launch template.

set -euxo pipefail
exec > >(tee /var/log/user-data.log | logger -t user-data) 2>&1

# ---------- CONFIG ----------
REGION="us-east-1"
S3_JAR_URI="s3://CHANGE-ME-artifacts-bucket/app.jar"
DB_SECRET_ID="CHANGE-ME-rds-secret-name"
DB_HOST="CHANGE-ME.abcdefghijkl.us-east-1.rds.amazonaws.com"
DB_NAME="appdb"
# ----------------------------

dnf install -y java-17-amazon-corretto-headless

id -u springboot &>/dev/null || useradd -r -s /sbin/nologin springboot
install -d -o springboot -g springboot /opt/app

aws s3 cp "$S3_JAR_URI" /opt/app/app.jar --region "$REGION"
chown springboot:springboot /opt/app/app.jar

SECRET_JSON="$(aws secretsmanager get-secret-value \
  --secret-id "$DB_SECRET_ID" \
  --region "$REGION" \
  --query SecretString --output text)"
DB_USER="$(printf '%s' "$SECRET_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["username"])')"
DB_PASS="$(printf '%s' "$SECRET_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')"

# Written before the unit starts so the password never appears in the process list.
umask 077
cat > /etc/app.env <<EOF
SERVER_PORT=8080
SPRING_DATASOURCE_URL=jdbc:mysql://${DB_HOST}:3306/${DB_NAME}?useSSL=true&requireSSL=true&serverTimezone=UTC
SPRING_DATASOURCE_USERNAME=${DB_USER}
SPRING_DATASOURCE_PASSWORD=${DB_PASS}
EOF
chown root:springboot /etc/app.env
chmod 640 /etc/app.env

cat > /etc/systemd/system/app.service <<'UNIT'
[Unit]
Description=Spring Boot Application
After=network-online.target
Wants=network-online.target

[Service]
User=springboot
EnvironmentFile=/etc/app.env
ExecStart=/usr/bin/java -XX:MaxRAMPercentage=75 -jar /opt/app/app.jar
# Spring Boot exits with 143 on SIGTERM; without this systemd logs a false failure.
SuccessExitStatus=143
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now app.service
