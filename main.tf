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
  tenancy_ocid = var.tenancy_ocid
  region       = "us-chicago-1"
}

variable "tenancy_ocid" {
  description = "The OCID of your tenancy."
  default     = "ocid1.tenancy.oc1..aaaaaaaaiyavtwbz4kyu7g7b6wglllccbflmjx2lzk5nwpbme44mv54xu7dq"
}

variable "compartment_ocid" {
  description = "The OCID of the compartment to deploy resources into."
  default     = "ocid1.compartment.oc1..aaaaaaaai2xobwjbjjhudkkiyu4ilnozjcpw5zx7n2qrgywnzs2akanabnsq"
}

variable "app_image_url" {
  description = "The full URL of the web app Docker image in OCIR."
  default     = "ord.ocir.io/orasenatdpltintegration03/oci-image-upload-app-ajd:delete-fix-v3"
}

variable "function_image_url" {
  description = "The full URL of the function Docker image in OCIR."
  default     = "ord.ocir.io/orasenatdpltintegration03/vision-analyzer-func-ajd:commit-fix"
}

variable "bucket_name" {
  description = "The base name for the object storage buckets."
  default     = "oci-image-analysis-bucket"
}

# ---------------------------------------------------------------------------
# Data Sources
# ---------------------------------------------------------------------------
data "oci_identity_availability_domains" "ad" {
  compartment_id = var.tenancy_ocid
}

data "oci_objectstorage_namespace" "ns" {}

data "oci_core_services" "all_services" {}

locals {
  all_services_in_network = [
    for service in data.oci_core_services.all_services.services : service
    if service.name == "All ORD Services In Oracle Services Network"
  ]
}

# ---------------------------------------------------------------------------
# Networking (Two-Subnet Architecture with LB and NAT)
# ---------------------------------------------------------------------------
resource "oci_core_vcn" "vision_vcn" {
  compartment_id = var.compartment_ocid
  display_name   = "vision-app-vcn"
  cidr_block     = "10.0.0.0/16"
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
}

resource "oci_core_subnet" "private_subnet" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.vision_vcn.id
  display_name      = "vision-app-private-subnet"
  cidr_block        = "10.0.2.0/24"
  route_table_id    = oci_core_route_table.private_rt.id
  security_list_ids = [oci_core_security_list.app_sl.id]
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
resource "oci_database_autonomous_database" "vision_json_db" {
  compartment_id      = var.compartment_ocid
  db_name             = "visionjsondb"
  display_name        = "VisionJsonDB"
  admin_password      = random_password.db_admin_password.result
  db_workload         = "AJD"
  cpu_core_count      = 1
  data_storage_size_in_tbs = 1
  whitelisted_ips     = ["0.0.0.0/0"]
  db_version          = "23ai"
  license_model       = "LICENSE_INCLUDED"
}

resource "random_password" "db_admin_password" {
  length      = 16
  special     = true
  number      = true
  upper       = true
  lower       = true
  min_numeric = 1
  min_upper   = 1
  min_lower   = 1
  min_special = 1
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
  application_id        = oci_functions_application.vision_app.id
  display_name          = "vision-analyzer-func-ajd"
  image                 = var.function_image_url
  memory_in_mbs         = 512
  timeout_in_seconds    = 300
  config = {
    DB_CONNECTION_STRING = oci_database_autonomous_database.vision_json_db.connection_strings[0].profiles[2].value # LOW TNS
    DB_PASSWORD         = random_password.db_admin_password.result
    THICK_MODE_UPDATE   = "2025-08-06-x86-fix"
  }
}

# ---------------------------------------------------------------------------
# Event Rule to Trigger Function
# ---------------------------------------------------------------------------
resource "oci_events_rule" "image_upload_event_rule" {
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
      function_id = oci_functions_function.vision_function.id
      is_enabled  = true
    }
  }
}

# ---------------------------------------------------------------------------
# OCI Container Instance (Web App)
# ---------------------------------------------------------------------------
resource "oci_container_instances_container_instance" "oci_image_app_instance" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ad.availability_domains[0].name
  display_name        = "oci-image-upload-app-ajd"
  shape               = "CI.Standard.E4.Flex"
  shape_config {
    memory_in_gbs = 1
    ocpus         = 1
  }
  containers {
    image_url = var.app_image_url
    environment_variables = {
      DB_CONNECTION_STRING = oci_database_autonomous_database.vision_json_db.connection_strings[0].profiles[2].value # LOW TNS
      DB_PASSWORD         = random_password.db_admin_password.result
      THICK_MODE_UPDATE   = "2025-08-06-x86-fix"
    }
  }
  vnics {
    subnet_id             = oci_core_subnet.private_subnet.id
    is_public_ip_assigned = false
  }
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