# Terraform Configuration for OCI Event-Driven Vision AI Application

# ---------------------------------------------------------------------------
# Provider and Variables
# ---------------------------------------------------------------------------
terraform {
  required_providers {
    oci = {
      source = "oracle/oci"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.1.0"
    }
  }
}

provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

# OCI Provider Configuration Variables
variable "tenancy_ocid" {
  description = "The OCID of your tenancy (from OCI Console -> Tenancy Information)"
  type        = string
  validation {
    condition     = can(regex("^ocid1\\.tenancy\\.oc1\\.\\..*", var.tenancy_ocid))
    error_message = "The tenancy_ocid must be a valid OCI tenancy OCID starting with 'ocid1.tenancy.oc1..'."
  }
}

variable "user_ocid" {
  description = "The OCID of your OCI user (from OCI Console -> User Settings)"
  type        = string
  validation {
    condition     = can(regex("^ocid1\\.user\\.oc1\\.\\..*", var.user_ocid))
    error_message = "The user_ocid must be a valid OCI user OCID starting with 'ocid1.user.oc1..'."
  }
}

variable "fingerprint" {
  description = "The fingerprint of your OCI API key (from OCI Console -> User Settings -> API Keys)"
  type        = string
  validation {
    condition     = can(regex("^[0-9a-f]{2}(:[0-9a-f]{2}){15}$", var.fingerprint))
    error_message = "The fingerprint must be in the format xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx."
  }
}

variable "private_key_path" {
  description = "The path to your OCI private key file (e.g., ~/.oci/oci_api_key.pem)"
  type        = string
}

variable "region" {
  description = "The OCI region to deploy resources in (e.g., us-ashburn-1, ca-toronto-1)"
  type        = string
  default     = "ca-toronto-1"
}

variable "compartment_ocid" {
  description = "The OCID of the compartment to deploy resources into"
  type        = string
  validation {
    condition     = can(regex("^ocid1\\.compartment\\.oc1\\.\\..*", var.compartment_ocid))
    error_message = "The compartment_ocid must be a valid OCI compartment OCID starting with 'ocid1.compartment.oc1..'."
  }
}

variable "db_admin_password" {
  description = "Admin password to initialize the Autonomous JSON Database (meets ADB password policy)."
  type        = string
  sensitive   = true
}


variable "app_image_url" {
  description = "The full URL of the web app Docker image in OCIR (leave empty to auto-generate)"
  type        = string
  default     = ""
}

variable "function_image_url" {
  description = "The full URL of the function Docker image in OCIR (leave empty to auto-generate)"
  type        = string
  default     = ""
}

variable "use_placeholder_images" {
  description = "Use placeholder images for initial deployment (set to false after pushing real images)"
  type        = bool
  default     = true
}

variable "make_repositories_public" {
  description = "Make OCIR repositories public (true) or private (false)"
  type        = bool
  default     = true
}

variable "bucket_name" {
  description = "The base name for the object storage buckets."
  default     = "oci-image-analysis-bucket"
}

variable "availability_domain" {
  description = "Optional Availability Domain name (e.g., 'XYZ:US-ASHBURN-AD-1'). If unset, the first AD in-region is selected automatically."
  type        = string
  default     = ""
}

# Toggle to enable/disable ADB private endpoint
variable "enable_private_endpoint" {
  description = "When true, create Autonomous DB with a private endpoint in the private subnet; when false, use public endpoint (no IP allowlist)."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Data Sources
# ---------------------------------------------------------------------------
data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_objectstorage_namespace" "ns" {}

data "oci_core_services" "all_services" {}

# Fetch additional DB connection info including web URLs
data "oci_database_autonomous_database" "vision_json_db_data" {
  autonomous_database_id = oci_database_autonomous_database.vision_json_db.id
}

locals {
  # Pick provided AD or default to the first available in the region
  availability_domain = var.availability_domain != "" ? var.availability_domain : data.oci_identity_availability_domains.ads.availability_domains[0].name
  # Derive ORDS base URL from APEX URL (ends with 'apex'); replace with 'ords/'
  ords_url = replace(data.oci_database_autonomous_database.vision_json_db_data.connection_urls[0].apex_url, "apex", "")
  all_services_in_network = [
    for service in data.oci_core_services.all_services.services : service
    if strcontains(lower(service.name), "all") && strcontains(lower(service.name), "oracle") && strcontains(lower(service.name), "services")
  ]
  
  # OCIR configuration - map region to region key
  region_key = (
    split("-", var.region)[0] == "us" ? substr(var.region, 0, 3) :
    split("-", var.region)[0] == "ca" ? "yyz" :
    split("-", var.region)[0] == "uk" ? "lhr" :
    split("-", var.region)[0] == "eu" ? substr(var.region, 0, 3) :
    split("-", var.region)[0] == "ap" ? substr(var.region, 0, 3) :
    substr(var.region, 0, 3)
  )
  
  tenancy_namespace = data.oci_objectstorage_namespace.ns.namespace
  ocir_base_url = "${local.region_key}.ocir.io/${local.tenancy_namespace}"
  
  # Image configuration
  app_image_name = "oci-image-upload-app-ajd"
  function_image_name = "vision-analyzer-func-ajd"
  app_image_tag = "latest"
  function_image_tag = "latest"
  
  # Determine which images to use
  app_image_url = var.use_placeholder_images ? "busybox:latest" : (
    var.app_image_url != "" ? var.app_image_url : "${local.ocir_base_url}/${local.app_image_name}:${local.app_image_tag}"
  )
  
  function_image_url = var.use_placeholder_images ? "busybox:latest" : (
    var.function_image_url != "" ? var.function_image_url : "${local.ocir_base_url}/${local.function_image_name}:${local.function_image_tag}"
  )
}

# ---------------------------------------------------------------------------
# Oracle Container Image Registry (OCIR) Repositories
# ---------------------------------------------------------------------------
resource "oci_artifacts_container_repository" "app_repository" {
  compartment_id   = var.compartment_ocid
  display_name     = local.app_image_name
  is_immutable     = false
  is_public        = var.make_repositories_public
  readme {
    content = "Web application for OCI Vision AI image analysis"
    format  = "text/plain"
  }
}

resource "oci_artifacts_container_repository" "function_repository" {
  compartment_id   = var.compartment_ocid
  display_name     = local.function_image_name
  is_immutable     = false
  is_public        = var.make_repositories_public
  readme {
    content = "OCI Function for AI Vision image processing"
    format  = "text/plain"
  }
}

# ---------------------------------------------------------------------------
# Networking (Two-Subnet Architecture with LB and NAT)
# ---------------------------------------------------------------------------
resource "oci_core_vcn" "vision_vcn" {
  compartment_id = var.compartment_ocid
  display_name   = "vision-app-vcn"
  cidr_block     = "10.0.0.0/16"
  # Required for private endpoints: VCN must have a DNS label
  dns_label      = "visionvcn"
}

resource "oci_core_internet_gateway" "vision_ig" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.vision_vcn.id
  display_name   = "vision-app-ig"
}

resource "oci_core_service_gateway" "vision_sg" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.vision_vcn.id
  display_name   = "vision-app-sg"
  services {
    service_id = local.all_services_in_network[0].id
  }
}

resource "oci_core_nat_gateway" "vision_ng" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.vision_vcn.id
  display_name   = "vision-app-ng"
}

resource "oci_core_route_table" "public_rt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.vision_vcn.id
  display_name   = "vision-app-public-rt"
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.vision_ig.id
  }
}

resource "oci_core_route_table" "private_rt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.vision_vcn.id
  display_name   = "vision-app-private-rt"
  route_rules {
    destination_type  = "SERVICE_CIDR_BLOCK"
    destination       = local.all_services_in_network[0].cidr_block
    network_entity_id = oci_core_service_gateway.vision_sg.id
  }
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.vision_ng.id
  }
}

resource "oci_core_security_list" "lb_sl" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.vision_vcn.id
  display_name   = "vision-app-lb-sl"
  ingress_security_rules {
    protocol  = "6" # TCP
    source    = "0.0.0.0/0"
    stateless = false
    tcp_options {
      max = 80
      min = 80
    }
  }
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
    stateless   = false
  }
}

resource "oci_core_security_list" "app_sl" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.vision_vcn.id
  display_name   = "vision-app-app-sl"
  ingress_security_rules {
    protocol  = "6" # TCP
    source    = oci_core_subnet.public_subnet.cidr_block
    stateless = false
    tcp_options {
      max = 5000
      min = 5000
    }
  }
  # Allow private subnet workloads to reach Autonomous DB private endpoint over HTTPS (ORDS)
  ingress_security_rules {
    protocol  = "6" # TCP
    # Avoid dependency cycle by using literal CIDR (not subnet reference)
    source    = "10.0.2.0/24"
    stateless = false
    tcp_options {
      max = 443
      min = 443
    }
  }
  # Optional: allow SQL*Net to ADB private endpoint if thick client is used
  ingress_security_rules {
    protocol  = "6" # TCP
    # Avoid dependency cycle by using literal CIDR (not subnet reference)
    source    = "10.0.2.0/24"
    stateless = false
    tcp_options {
      max = 1522
      min = 1522
    }
  }
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
    stateless   = false
  }
}

resource "oci_core_subnet" "public_subnet" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.vision_vcn.id
  display_name      = "vision-app-public-subnet"
  cidr_block        = "10.0.1.0/24"
  route_table_id    = oci_core_route_table.public_rt.id
  security_list_ids = [oci_core_security_list.lb_sl.id]
  # DNS label required when using private endpoints in the VCN
  dns_label         = "public"
}

resource "oci_core_subnet" "private_subnet" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.vision_vcn.id
  display_name      = "vision-app-private-subnet"
  cidr_block        = "10.0.2.0/24"
  route_table_id    = oci_core_route_table.private_rt.id
  security_list_ids = [oci_core_security_list.app_sl.id]
  # DNS label required for Autonomous DB private endpoint subnet
  dns_label         = "private"
}

# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------
resource "oci_objectstorage_bucket" "uploads_bucket" {
  compartment_id        = var.compartment_ocid
  name                  = var.bucket_name
  namespace             = data.oci_objectstorage_namespace.ns.namespace
  object_events_enabled = true
}

# ---------------------------------------------------------------------------
# Autonomous JSON Database
# ---------------------------------------------------------------------------
resource "null_resource" "private_ep_toggle" {
  # Changes whenever the toggle flips, used to force DB replacement
  triggers = {
    mode = var.enable_private_endpoint ? "private" : "public"
  }
}

resource "oci_database_autonomous_database" "vision_json_db" {
  lifecycle {
    create_before_destroy = true
    # Force replacement when the private/public toggle changes
    replace_triggered_by = [null_resource.private_ep_toggle]
  }
  compartment_id      = var.compartment_ocid
  db_name             = "visionjsondb"
  display_name        = "VisionJsonDB"
  admin_password      = var.db_admin_password
  db_workload         = "AJD"
  cpu_core_count      = 1
  data_storage_size_in_tbs = 1
  # Network access: private endpoint when enabled; otherwise public endpoint
  # Note: Do NOT set access-control flags for public mode; omit to use default (open)
  subnet_id                          = var.enable_private_endpoint ? oci_core_subnet.private_subnet.id : null
  private_endpoint_label             = var.enable_private_endpoint ? "visionjsondb-pe" : null
  db_version          = "23ai"
  license_model       = "LICENSE_INCLUDED"
}

# ---------------------------------------------------------------------------
# Vault, Key Management, and Secrets (for ORDS credentials)
# ---------------------------------------------------------------------------
resource "oci_kms_vault" "vision_vault" {
  compartment_id = var.compartment_ocid
  display_name   = "vision-app-vault"
  vault_type     = "DEFAULT"
}

resource "oci_kms_key" "vision_vault_key" {
  compartment_id      = var.compartment_ocid
  display_name        = "vision-app-vault-key"
  # AES-256 symmetric key
  key_shape {
    algorithm = "AES"
    length    = 32
  }
  management_endpoint = oci_kms_vault.vision_vault.management_endpoint
}

# Secret for ORDS DB password
resource "oci_vault_secret" "db_password_secret" {
  compartment_id = var.compartment_ocid
  secret_name    = "db_password"
  description    = "ORDS database password (ADMIN or least-privileged user)"
  vault_id       = oci_kms_vault.vision_vault.id
  key_id         = oci_kms_key.vision_vault_key.id

  secret_content {
    content_type = "BASE64"
    name         = "current"
    stage        = "CURRENT"
    content      = base64encode(var.db_admin_password)
  }
}

# Secret for ORDS DB username (defaults to ADMIN)
resource "oci_vault_secret" "db_username_secret" {
  compartment_id = var.compartment_ocid
  secret_name    = "db_username"
  description    = "ORDS database username (prefer least-privileged over ADMIN)"
  vault_id       = oci_kms_vault.vision_vault.id
  key_id         = oci_kms_key.vision_vault_key.id

  secret_content {
    content_type = "BASE64"
    name         = "current"
    stage        = "CURRENT"
    content      = base64encode("ADMIN")
  }
}

# ---------------------------------------------------------------------------
# OCI Function
# ---------------------------------------------------------------------------
resource "oci_functions_application" "vision_app" {
  compartment_id = var.compartment_ocid
  display_name   = "vision-application"
  subnet_ids     = [oci_core_subnet.private_subnet.id]
  shape          = "GENERIC_X86"
}

resource "oci_functions_function" "vision_function" {
  count                 = var.use_placeholder_images ? 0 : 1
  application_id        = oci_functions_application.vision_app.id
  display_name          = "vision-analyzer-func-ajd"
  image                 = local.function_image_url
  memory_in_mbs         = 512
  timeout_in_seconds    = 300
  config = {
    DB_CONNECTION_STRING    = oci_database_autonomous_database.vision_json_db.connection_strings[0].profiles[2].value # LOW TNS
    THICK_MODE_UPDATE       = "2025-08-06-x86-fix"
    TENANCY_OCID            = var.tenancy_ocid
    # Provide ORDS base URL so the function does not rely on hardcoded default
    DB_ORDS_BASE_URL        = local.ords_url
    # Provide secret OCIDs so the function can fetch from OCI Vault
    DB_PASSWORD_SECRET_OCID = oci_vault_secret.db_password_secret.id
    DB_USERNAME_SECRET_OCID = oci_vault_secret.db_username_secret.id
  }
  
  depends_on = [oci_artifacts_container_repository.function_repository]
}

# ---------------------------------------------------------------------------
# Event Rule to Trigger Function
# ---------------------------------------------------------------------------
resource "oci_events_rule" "image_upload_event_rule" {
  count          = var.use_placeholder_images ? 0 : 1
  compartment_id = var.compartment_ocid
  display_name   = "image-upload-trigger"
  is_enabled     = true
  condition = jsonencode({
    "eventType" : ["com.oraclecloud.objectstorage.createobject"],
    "data" : {
      "additionalDetails" : {
        "bucketName" : var.bucket_name
      }
    }
  })
  actions {
    actions {
      action_type = "FAAS"
      function_id = oci_functions_function.vision_function[0].id
      is_enabled  = true
    }
  }
}

# ---------------------------------------------------------------------------
# OCI Container Instance (Web App)
# ---------------------------------------------------------------------------
resource "oci_container_instances_container_instance" "oci_image_app_instance" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.availability_domain
  display_name        = "oci-image-upload-app-ajd"
  shape               = "CI.Standard.E4.Flex"
  shape_config {
    memory_in_gbs = 1
    ocpus         = 1
  }
  containers {
    image_url = local.app_image_url
    environment_variables = var.use_placeholder_images ? {} : {
      # Keep for backward compatibility; app ignores these when secrets are present
      DB_CONNECTION_STRING     = oci_database_autonomous_database.vision_json_db.connection_strings[0].profiles[2].value # LOW TNS
      THICK_MODE_UPDATE        = "2025-08-06-x86-fix"
      DB_ORDS_BASE_URL         = local.ords_url
      # Secret OCIDs for app to fetch via Resource Principals + Secrets service
      DB_PASSWORD_SECRET_OCID  = oci_vault_secret.db_password_secret.id
      DB_USERNAME_SECRET_OCID  = oci_vault_secret.db_username_secret.id
      # Optional: provide DB_ORDS_URL_SECRET_OCID if you store ORDS base URL as a secret
      # DB_ORDS_URL_SECRET_OCID = oci_secrets_secret.db_ords_url_secret.id
    }
  }
  vnics {
    subnet_id             = oci_core_subnet.private_subnet.id
    is_public_ip_assigned = false
  }
  
  depends_on = [oci_artifacts_container_repository.app_repository]
}

# ---------------------------------------------------------------------------
# OCI Load Balancer
# ---------------------------------------------------------------------------
resource "oci_load_balancer_load_balancer" "app_lb" {
  compartment_id = var.compartment_ocid
  display_name   = "vision-app-lb"
  shape          = "flexible"
  shape_details {
    minimum_bandwidth_in_mbps = 10
    maximum_bandwidth_in_mbps = 10
  }
  is_private = false
  subnet_ids = [oci_core_subnet.public_subnet.id]
}

resource "oci_load_balancer_backend_set" "app_bs" {
  name             = "vision-app-bs"
  load_balancer_id = oci_load_balancer_load_balancer.app_lb.id
  policy           = "ROUND_ROBIN"
  health_checker {
    protocol    = "HTTP"
    port        = 5000
    url_path    = "/"
    return_code = 200
  }
}

resource "oci_load_balancer_backend" "app_backend" {
  load_balancer_id = oci_load_balancer_load_balancer.app_lb.id
  backendset_name  = oci_load_balancer_backend_set.app_bs.name
  ip_address       = oci_container_instances_container_instance.oci_image_app_instance.vnics[0].private_ip
  port             = 5000
  backup           = false
  drain            = false
  offline          = false
  weight           = 1
  
  # Ensure backend is recreated when container instance changes
  depends_on = [oci_container_instances_container_instance.oci_image_app_instance]
}

resource "oci_load_balancer_listener" "app_listener" {
  load_balancer_id         = oci_load_balancer_load_balancer.app_lb.id
  name                     = "http-listener"
  default_backend_set_name = oci_load_balancer_backend_set.app_bs.name
  port                     = 80
  protocol                 = "HTTP"
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
output "application_url" {
  description = "The public URL for the web application."
  value       = "http://${oci_load_balancer_load_balancer.app_lb.ip_address_details[0].ip_address}"
}

# OCIR and Container Build Information
output "tenancy_namespace" {
  description = "The tenancy namespace for OCIR repositories"
  value       = local.tenancy_namespace
}

output "region_key" {
  description = "The region key for OCIR URLs"
  value       = local.region_key
}

output "ocir_base_url" {
  description = "The base OCIR URL for this tenancy"
  value       = local.ocir_base_url
}

output "app_image_repository_url" {
  description = "Full OCIR URL for the web application image"
  value       = "${local.ocir_base_url}/${local.app_image_name}"
}

output "function_image_repository_url" {
  description = "Full OCIR URL for the function image"
  value       = "${local.ocir_base_url}/${local.function_image_name}"
}

output "app_image_full_url" {
  description = "Complete image URL with tag for the web application"
  value       = "${local.ocir_base_url}/${local.app_image_name}:${local.app_image_tag}"
}

output "function_image_full_url" {
  description = "Complete image URL with tag for the function"
  value       = "${local.ocir_base_url}/${local.function_image_name}:${local.function_image_tag}"
}

output "deployment_summary" {
  description = "Summary of key deployment information"
  value = {
    application_url = "http://${oci_load_balancer_load_balancer.app_lb.ip_address_details[0].ip_address}"
    tenancy_namespace = local.tenancy_namespace
    region_key = local.region_key
    app_repository = oci_artifacts_container_repository.app_repository.display_name
    function_repository = oci_artifacts_container_repository.function_repository.display_name
    database_name = oci_database_autonomous_database.vision_json_db.db_name
    next_steps = [
      "1. Update database URLs in app.py and func.py with correct ORDS endpoint",
      "2. Use the build_commands output to build and push your container images", 
      "3. Run terraform apply again to deploy with the new images"
    ]
  }
}

# List of build commands for both container web app and function sequential execution
output "build_commands" {
  description = "Podman/Docker commands to build and push images"
  value = [
    "echo 'YOUR_AUTH_TOKEN' | podman login ${local.region_key}.ocir.io --username '${local.tenancy_namespace}/YOUR_USERNAME' --password-stdin",
    "podman build --platform=linux/amd64 -t ${local.app_image_name}:${local.app_image_tag} -f Dockerfile .",
    "podman tag ${local.app_image_name}:${local.app_image_tag} ${local.ocir_base_url}/${local.app_image_name}:${local.app_image_tag}",
    "podman push ${local.ocir_base_url}/${local.app_image_name}:${local.app_image_tag}",
    "podman build --platform=linux/amd64 -t ${local.function_image_name}:${local.function_image_tag} -f vision_function/Dockerfile .",
    "podman tag ${local.function_image_name}:${local.function_image_tag} ${local.ocir_base_url}/${local.function_image_name}:${local.function_image_tag}",
    "podman push ${local.ocir_base_url}/${local.function_image_name}:${local.function_image_tag}"
  ]
}

output "database_info" {
  description = "Slim database info relevant to this project"
  value = {
    ords_url = local.ords_url
  }
}

output "ords_url" {
  description = "ORDS base URL for the Autonomous Database (ends with /ords/)"
  value       = local.ords_url
}

# Secrets and Vault outputs (for reference and wiring)
output "vault_id" {
  description = "OCI Vault OCID used for storing secrets"
  value       = oci_kms_vault.vision_vault.id
}

output "db_password_secret_ocid" {
  description = "OCID of the secret holding the ORDS DB password"
  value       = oci_vault_secret.db_password_secret.id
  sensitive   = true
}

output "db_username_secret_ocid" {
  description = "OCID of the secret holding the ORDS DB username"
  value       = oci_vault_secret.db_username_secret.id
}
