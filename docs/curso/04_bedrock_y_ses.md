# Módulo 04 — Bedrock y SES

## Objetivo

Entender por qué la policy de Bedrock lista **dos** ARNs (un inference profile y un foundation model), y por qué SES en sandbox requiere verificar **tanto** sender como recipient, y por qué la policy también lista ambos.

## Conceptos — Bedrock

**Foundation model (FM).** El modelo "crudo" de Anthropic hosteado en Bedrock: `anthropic.claude-haiku-4-5-20251001-v1:0`.

**Inference profile.** Una capa de routing: `us.anthropic.claude-haiku-4-5-20251001-v1:0`. El prefijo `us.` indica **cross-region inference** — AWS puede enrutar la invocación a cualquier región de EE. UU. donde el FM esté disponible.

**Cuando invocas un inference profile, IAM valida permisos contra el profile Y contra el FM subyacente.** Por eso la policy lista **ambos** ARNs:

```json
"Resource": [
  "arn:aws:bedrock:us-east-1:930067561911:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
  "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"
]
```

Sin el segundo, `AccessDeniedException: cannot invoke foundation-model`.

## Conceptos — SES

**Identities.** Direcciones de correo o dominios verificados. Sin verificar, no puedes enviar desde ni a esa dirección (en sandbox).

**Sandbox.** Estado inicial de SES en cualquier cuenta. Restricciones:
- Solo puedes enviar **desde** identities verificadas.
- Solo puedes enviar **hacia** identities verificadas.
- Cap de 200 correos/día, 1/segundo.

**Producción.** Se sale por ticket a AWS Support. Ahí solo necesitas verificar el sender.

### Por qué la policy lista ambas identities

En producción bastaría con:

```json
{ "Action": "ses:SendRawEmail", "Resource": "arn:aws:ses:...identity/<sender>" }
```

Pero en sandbox, SES valida **ambas** identities contra el `Resource` de la policy. Si falta el recipient, obtienes `AccessDenied: not authorized to perform ses:SendRawEmail on resource arn:...identity/<recipient>`.

**Fix aplicado:**

```json
{
  "Action": "ses:SendRawEmail",
  "Resource": [
    "arn:aws:ses:us-east-1:930067561911:identity/1206029@onuriscp.com",
    "arn:aws:ses:us-east-1:930067561911:identity/samsalriu@gmail.com"
  ],
  "Condition": {
    "StringEquals": { "ses:FromAddress": "1206029@onuriscp.com" }
  }
}
```

El `Condition` ancla que el sender real es `1206029@...` — sin él, alguien con acceso al rol podría enviar usando `samsalriu@...` como sender.

## Track A — Inspección real

```bash
# Modelos Bedrock disponibles
aws bedrock list-foundation-models --region us-east-1 \
  --query 'modelSummaries[?contains(modelId, `haiku-4-5`)].{id:modelId,providers:providerName}'

# Inference profiles
aws bedrock list-inference-profiles --region us-east-1 \
  --query 'inferenceProfileSummaries[?contains(inferenceProfileId, `haiku-4-5`)].{id:inferenceProfileId,models:models[].modelArn}'

# SES identities verificadas
aws ses list-identities --query 'Identities'

# Estado de cada identity
aws ses get-identity-verification-attributes --identities 1206029@onuriscp.com samsalriu@gmail.com

# Estado de sandbox
aws sesv2 get-account \
  --query '{sandbox:ProductionAccessEnabled,sendQuota:SendQuota}'
```

## Invocación manual de Bedrock (smoke test)

```bash
aws bedrock-runtime invoke-model \
  --model-id "us.anthropic.claude-haiku-4-5-20251001-v1:0" \
  --content-type application/json \
  --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":50,"messages":[{"role":"user","content":"Hola en 5 palabras"}]}' \
  /tmp/bedrock-out.json
cat /tmp/bedrock-out.json | jq '.content[0].text'
```

## Problemas que encontré

- **SES AccessDenied con solo sender en Resource.** Lección arriba. Tardó ~15 min diagnosticarlo porque el error mencionaba el recipient ARN como recurso denegado — no el sender como uno podría esperar.
- **Bedrock `AccessDeniedException` con solo el inference profile.** Resuelto agregando el FM ARN con wildcard de región (`bedrock:*::foundation-model/...`). La región es `*` porque cross-region inference puede enrutar a cualquier región US.

## Ejercicios

1. Invoca Bedrock manualmente con el comando de arriba. Obtén una respuesta.
2. Lee `ses:FromAddress` en la [doc de condiciones SES](https://docs.aws.amazon.com/ses/latest/dg/sending-authorization-policy-examples.html) y explica qué pasaría sin ese `Condition`.
3. ¿Qué pasa si intentas mandar correo a una identity no verificada estando en sandbox? (Pruébalo con `aws ses send-email`.)

## Checklist de dominio

- [ ] Sé qué es un inference profile y por qué se usa.
- [ ] Puedo explicar por qué la policy de Bedrock lista 2 ARNs.
- [ ] Sé qué es SES sandbox y sus límites.
- [ ] Puedo explicar por qué la policy de SES lista 2 identities.
- [ ] Entiendo el rol del `Condition ses:FromAddress`.

## Referencias

- [Bedrock cross-region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)
- [SES sending authorization](https://docs.aws.amazon.com/ses/latest/dg/sending-authorization.html)
