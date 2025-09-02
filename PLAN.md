# Plan

Summary
- We migrated DB credentials to OCI Vault and fetch them via Resource Principals in both the web app and function. Terraform now provisions Vault, Key, and Secrets and passes secret OCIDs to workloads.

Next Steps
- IAM: Add policies in Identity Domain for DGs
  - Allow dynamic-group <DOMAIN_NAME>/WebAppInstanceDynamicGroup to read secret-family in compartment id <COMPARTMENT_OCID>
  - Allow dynamic-group <DOMAIN_NAME>/WebAppInstanceDynamicGroup to use keys in compartment id <COMPARTMENT_OCID>
  - Allow dynamic-group <DOMAIN_NAME>/VisionFunctionDynamicGroup to read secret-family in compartment id <COMPARTMENT_OCID>
  - Allow dynamic-group <DOMAIN_NAME>/VisionFunctionDynamicGroup to use keys in compartment id <COMPARTMENT_OCID>
- Build & Push Images
  - Web app and function images; use `terraform output build_commands` for exact commands
- Apply Infra
  - `terraform init -upgrade && terraform apply`
- Verify Runtime
  - Web app logs show: Loaded DB_USERNAME/DB_PASSWORD from Vault
  - Function logs show: Attempting to load DB config from OCI Vault… then Loaded DB_PASSWORD from Vault

Optional Enhancements
- Store ORDS base URL in Vault; set `DB_ORDS_URL_SECRET_OCID` for app and function
- Create a least-privileged DB user for ORDS instead of ADMIN; rotate password in Vault
- Remove hardcoded default creds/URL after secrets are fully in place

Validation Checklist
- Upload image → Function triggers → Vision analysis succeeds
- JSON result saved to Autonomous JSON DB and visible in web UI
- Delete action removes both Object Storage object and DB document(s)

Changed Files
- main.tf (Vault/Key/Secrets, env wiring, function config)
- app/app.py (Vault secret retrieval)
- vision_function/func.py (Vault secret retrieval)
- README.md (Vault option + IAM notes)

