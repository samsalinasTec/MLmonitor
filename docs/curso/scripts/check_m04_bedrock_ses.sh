#!/usr/bin/env bash
# Módulo 04 — verifica Bedrock + SES.
set -u
pass() { echo "✅ $1"; }
fail() { echo "❌ $1"; FAILED=1; }
FAILED=0

aws bedrock list-foundation-models --region us-east-1 \
  --query 'modelSummaries[?modelId==`anthropic.claude-haiku-4-5-20251001-v1:0`].modelId' --output text 2>/dev/null \
  | grep -q haiku && pass "Bedrock foundation model Haiku 4.5 accesible" || fail "Bedrock model no listado"

aws bedrock list-inference-profiles --region us-east-1 \
  --query 'inferenceProfileSummaries[?inferenceProfileId==`us.anthropic.claude-haiku-4-5-20251001-v1:0`].inferenceProfileId' --output text 2>/dev/null \
  | grep -q haiku && pass "Inference profile us.anthropic.claude-haiku-4-5 existe" || fail "Inference profile no encontrado"

for ID in 1206029@onuriscp.com samsalriu@gmail.com; do
  STATUS=$(aws ses get-identity-verification-attributes --identities $ID --query "VerificationAttributes.\"$ID\".VerificationStatus" --output text 2>/dev/null)
  [ "$STATUS" = "Success" ] && pass "SES identity $ID verificada" || fail "SES identity $ID no verificada: $STATUS"
done

exit $FAILED
