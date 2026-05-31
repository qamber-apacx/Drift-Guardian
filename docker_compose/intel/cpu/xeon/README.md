# Deploy PolicyDriftCheck on Intel Xeon (CPU)

This document describes how to deploy the PolicyDriftCheck application on an
Intel Xeon server using Docker Compose. The pipeline serves the LLM with
[Text Generation Inference (TGI)](https://github.com/huggingface/text-generation-inference)
on CPU.

## Services

| Service | Container | Port | Description |
| --- | --- | --- | --- |
| `tgi-service` | `policydriftcheck-tgi-service` | 8008 | LLM serving (TGI) |
| `policydriftcheck-backend-server` | `policydriftcheck-backend-server` | 8888 | MegaService gateway (REST API) |
| `policydriftcheck-ui-server` | `policydriftcheck-ui-server` | 5173 | Flask web interface |

## Prerequisites

- Docker and Docker Compose installed.
- A Hugging Face token with access to the chosen model (for gated models such
  as Llama 3).

## Build the images

```bash
git clone https://github.com/opea-project/GenAIExamples.git
cd GenAIExamples/PolicyDriftCheck/docker_image_build
docker compose -f build.yaml build --no-cache
```

## Configure the environment

```bash
cd ../docker_compose/intel/cpu/xeon
export HUGGINGFACEHUB_API_TOKEN="your_hf_token"
source set_env.sh
```

## Start the services

```bash
docker compose up -d
```

The TGI container downloads and loads the model on first run, which can take a
few minutes. Monitor progress with:

```bash
docker logs policydriftcheck-tgi-service
```

## Validate

```bash
# A SOP that drifts from the global policy on retention period.
echo "Records must be retained for 7 years. Access requires two-factor authentication." > global.txt
echo "Records are retained for 3 years. Access requires a password." > sop.txt

curl http://${host_ip}:8888/v1/drift_check \
  -X POST \
  -F "global_doc=@global.txt" \
  -F "sop_doc=@sop.txt"
```

Expected: a JSON response with `"verdict": "BLOCK"` and one or more findings.

Open the web interface at `http://${host_ip}:5173`.

## Stop the services

```bash
docker compose down
```
