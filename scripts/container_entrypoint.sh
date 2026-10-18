#!/usr/bin/env bash
set -euo pipefail
export QUANT_MAIL_MODE=simulate
unset QUANT_SMTP_PASSWORD QUANT_SMTP_USER QUANT_SMTP_HOST QUANT_SMTP_PORT QUANT_SMTP_SECURITY
exec "$@"
