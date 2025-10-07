# How to Build a Serverless Image Analysis Pipeline on OCI

This project implements a full, event-driven pipeline on OCI. A web application uploads an image to an object storage bucket, which triggers an OCI function for object detection using OCI's AI Vision service. The JSON results are then stored as documents in an **Oracle Autonomous JSON Database** via Oracle REST Data Service (ORDS) and displayed in the web app.

For more information and detailed step-by-step instructions visit my [How to Build a Serverless Image Analysis Pipeline on OCI](https://www.cloudnativenotes.com/post/ocivision-serverless-pipeline/) blog post.  

**Key Features:**
- **Wallet-free deployment**: Uses Oracle REST Data Services (ORDS) for simplified database access
- **Resource Principal authentication**: No credential management required
- **Event-driven architecture**: Automatic processing when images are uploaded
- **Complete data consistency**: Proper cleanup of both storage and database records

## WorkflowDiagram
<img src="/images/visionWorkflow.jpg" alt="Vision Function Workflow" width="1024">

---

## Prerequisites

Before you begin, you will need:
*   An OCI Account with permissions to manage IAM policies, Dynamic Groups, VCNs, Container Instances, Functions, Object Storage, and Autonomous Databases.
*   **Terraform:** To provision the cloud infrastructure.
*   **OCI CLI:** Configured with your user credentials (`oci setup config`). This is required for local development and deployment.
*   **Podman (or Docker):** To build and push container images. **Note: This project requires ARM64 compatibility fixes, so use `--platform=linux/amd64` for all builds.**

---

## Part 1: Manual IAM Configuration

This is a critical step. You must create the correct IAM policies and dynamic groups to allow your applications to securely authenticate to other OCI services.

**CRITICAL NOTE for Tenancies with IAM Identity Domains:** If your tenancy uses IAM Identity Domains (formerly IDCS), you **MUST** create the Dynamic Groups and Policies inside your specific domain.

1.  **Navigate to your Identity Domain:**
    *   In the OCI Console, navigate to `Identity & Security` -> **`Domains`**.
    *   Click on your specific domain (e.g., `Default`, `OracleIdentityCloudService`).
    *   All of the following steps must be performed within this domain's interface.

2.  **Create Dynamic Groups:**
    *   Inside your domain, navigate to **`Dynamic groups`**.
    *   Create two groups:
        *   **Name:** `WebAppInstanceDynamicGroup`
            *   **Matching Rule:** `ALL {resource.type = 'computecontainerinstance', resource.compartment.id = '<YOUR_COMPARTMENT_OCID>'}`
        *   **Name:** `VisionFunctionDynamicGroup`
            *   **Matching Rule:** `ALL {resource.type = 'fnfunc', resource.compartment.id = '<YOUR_COMPARTMENT_OCID>'}`
    *(Replace `<YOUR_COMPARTMENT_OCID>` with the OCID of your compartment).*

3.  **Create IAM Policy:**
    *   Navigate to **`Policies`**.
    *   Create a new policy named `VisionAppPolicy` in your compartment with the following statements.
    *   **Note the `<DOMAIN_NAME>/` prefix required for the dynamic group names.**

    ```
    # --- Permissions for the Function ---
    Allow dynamic-group <DOMAIN_NAME>/VisionFunctionDynamicGroup to use ai-service-vision-family in compartment id <YOUR_COMPARTMENT_OCID>
    Allow dynamic-group <DOMAIN_NAME>/VisionFunctionDynamicGroup to read objectstorage-namespaces in compartment id <YOUR_COMPARTMENT_OCID>
    Allow dynamic-group <DOMAIN_NAME>/VisionFunctionDynamicGroup to read compartments in compartment id <YOUR_COMPARTMENT_OCID>
    # Required for the function to connect to the database
    Allow dynamic-group <DOMAIN_NAME>/VisionFunctionDynamicGroup to use autonomous-databases in compartment id <YOUR_COMPARTMENT_OCID>
    Allow dynamic-group <DOMAIN_NAME>/VisionFunctionDynamicGroup to use database-family in compartment id <YOUR_COMPARTMENT_OCID>

    # --- Permissions for the Web App Container Instance ---
    Allow dynamic-group <DOMAIN_NAME>/WebAppInstanceDynamicGroup to manage object-family in compartment id <YOUR_COMPARTMENT_OCID> where target.bucket.name = 'oci-image-analysis-bucket'
    # Required for the web app to connect to the database
    Allow dynamic-group <DOMAIN_NAME>/WebAppInstanceDynamicGroup to use autonomous-databases in compartment id <YOUR_COMPARTMENT_OCID>
    Allow dynamic-group <DOMAIN_NAME>/WebAppInstanceDynamicGroup to use database-family in compartment id <YOUR_COMPARTMENT_OCID>

    # --- Permission for web app to read secrets and use keys from Vault Service ---
    Allow dynamic-group <DOMAIN_NAME>/WebAppInstanceDynamicGroup to read secret-family in compartment id <YOUR_COMPARTMENT_OCID>`
    Allow dynamic-group <DOMAIN_NAME>/WebAppInstanceDynamicGroup to use keys in compartment id <YOUR_COMPARTMENT_OCID>`

    # --- Permissions for logging  ---
    Allow service logging to use functions-family in compartment id <YOUR_COMPARTMENT_OCID>
    ```
    *(Replace `<DOMAIN_NAME>` with the name of your identity domain, e.g., `OracleIdentityCloudService`).*
    *(Replace `<YOUR_COMPARTMENT_OCID>` with the OCID of your compartment).*

---

## Part 2: Configure and Deploy Infrastructure

1.  **Configure Terraform Variables:**

    Copy the example variables file and update with your OCI credentials:
    ```bash
    cp terraform.tfvars.example terraform.tfvars
    ```

    Edit `terraform.tfvars` and provide:
    - **tenancy_ocid**: From OCI Console → Tenancy Information
    - **user_ocid**: From OCI Console → User Settings
    - **fingerprint**: From OCI Console → User Settings → API Keys
    - **private_key_path**: Path to your OCI API private key file
    - **region**: Your OCI region (e.g., ca-toronto-1)
    - **compartment_ocid**: Target compartment for resources

    Terraform will prompt you for any missing values.

    Network access toggle (Autonomous DB private endpoint)
    - `enable_private_endpoint` (boolean)
      - `true`: Creates the Autonomous JSON Database with a private endpoint in the private subnet (recommended for production).
      - `false`: Creates the database with a public endpoint (open; convenient for quick demos).
    - Switching from `false` → `true` causes replacement:
      - Terraform will create a new database and then destroy the old one (`create_before_destroy = true`).
      - Data is NOT preserved across this replacement. For production, plan a migration.
    - If you need to keep data, consider:
      - Export/import via SODA or Data Pump.
      - Side‑by‑side cutover: create a new private DB, repoint app/function, then decommission the old DB.

2.  **Initialize Terraform:**
    ```bash
    terraform init -upgrade
    ```

3.  **Apply Terraform (First Time):**
    ```bash
    terraform apply
    ```

    **Important:** This deployment will:
    - Create the Autonomous Database with REST API access
    - Create OCIR repositories for your container images
    - Set up complete networking and load balancer infrastructure
    - Output container build commands and image URLs

---

## Part 3: Configure Database REST API Access

The application uses Oracle REST Data Services (ORDS) to access the database using HTTP Basic Authentication.

1.  **Find Your Database ORDS URL:**

    **Manual Method:**
    *   In the OCI Console, navigate to **Oracle Database** → **Autonomous Database**.
    *   Find your database (`visionjsondb`) and click on it.
    *   Click the **"Tool configuration"** tab.
    *   Copy the **"Web Access (ORDS) Public access URL"**.
    *   This URL should look like: `https://[unique-id]-visionjsondb.adb.[region].oraclecloudapps.com/ords/`

2.  **Update Database Configuration:**

    **Manual Update:**
    *   **IMPORTANT**: Update the `DB_ORDS_BASE_URL` in both files to match your database's ORDS endpoint:
        *   **File 1**: `app/app.py` - Line ~19
        *   **File 2**: `vision_function/func.py` - Line ~13
        *   **Value**: Use the exact URL from step 1 above (including trailing slash)

    **URL Structure Reference:**
    ```
    Base ORDS URL:     https://[unique-id]-visionjsondb.adb.[region].oraclecloudapps.com/ords/
    SODA Endpoint:     {base-url}admin/soda/latest/
    Collection URL:    {soda-endpoint}IMAGE_ANALYSIS
    ```

3.  **Database Collection Setup:**

    **Automatic Setup**

    The application includes automatic collection creation in the Autonomous JSON Database via the `ensure_collection_exists()` function in both the web app and function. The `IMAGE_ANALYSIS` collection will be created automatically when first accessed. **No manual intervention required.**

    **Alternative Approach (For Reference Only)**

    ### Database Console Creation

    You can also create the collection through the Oracle Database Actions console:
    1. Access Database Actions from your Autonomous Database console
    2. Navigate to **JSON** section
    3. Create a new collection named `IMAGE_ANALYSIS`
    4. Configure with appropriate settings

---

## Part 4: Build and Deploy Container Images

**Automated OCIR Setup:** Terraform has already created the OCIR repositories and generated all the necessary commands for you!

1.  **Get Container Build Commands:**

    After running `terraform apply`, get the automated build commands:
    ```bash
    # View all build commands
    terraform output build_commands
    ```

2.  **OCIR Authentication Setup:**

    **Option A: Public Repositories (Default - Easier)**
    ```bash
    # With make_repositories_public = true, you still need to authenticate to push
    # but anyone can pull the images
    ```

    **Option B: Private Repositories (More Secure)**
    ```bash
    # With make_repositories_public = false, authentication required for both push and pull
    ```

    **Generate Auth Token (Required for Both Options):**
    *   In the OCI Console, go to **Profile** → **User Settings** → **Auth Tokens**
    *   Click **"Generate Token"**
    *   **Description:** "OCIR Docker Login"
    *   Copy the generated token immediately (it won't be shown again)

3.  **Log in to OCIR:**
    ```bash
    # Get your tenancy namespace and region key
    TENANCY_NAMESPACE=$(terraform output -raw tenancy_namespace)
    REGION_KEY=$(terraform output -raw region_key)

    # Login with your OCI username and auth token
    echo 'YOUR_AUTH_TOKEN' | podman login ${REGION_KEY}.ocir.io --username "${TENANCY_NAMESPACE}/your.email@domain.com" --password-stdin

    # Example:
    # echo 'A1B2C3...' | podman login yyz.ocir.io --username 'tenancy_name/user@atcompany.com' --password-stdin
    ```

    **Troubleshooting Login Issues:**
    ```bash
    # If you get 403 errors, verify:
    # 1. Auth token is correct and not expired
    # 2. Username format is exactly: tenancy_namespace/your_oci_username
    # 3. You have proper permissions in OCI

    # Test login
    podman login ${REGION_KEY}.ocir.io --get-login
    ```

4.  **Build and Push Images:**
    ```bash
    # Get repository URLs
    APP_IMAGE_URL=$(terraform output -raw app_image_full_url)
    FUNCTION_IMAGE_URL=$(terraform output -raw function_image_full_url)

    # Build and push web app
    podman build --platform=linux/amd64 -t oci-image-upload-app-ajd:latest -f Dockerfile .
    podman tag oci-image-upload-app-ajd:latest $APP_IMAGE_URL
    podman push $APP_IMAGE_URL

    # Build and push function
    podman build --platform=linux/amd64 -t vision-analyzer-func-ajd:latest -f vision_function/Dockerfile .
    podman tag vision-analyzer-func-ajd:latest $FUNCTION_IMAGE_URL
    podman push $FUNCTION_IMAGE_URL
    ```

**Important** This project uses a two-step deployment process

The first `terraform apply` uses placeholder images `(use_placeholder_images = true)` in `terrafrom.tfvars` to create all the necessary infrastructure, including the Container Repository (OCIR). You'll need to run `terraform apply` a second time to point the services to your new images.

After you've built and pushed your images:

- Double check that you’ve set your `terraform.tfvars` to `use_placeholder_images = false` as this updates the Web App container instance and Function to use the new IMAGE URL and pull from OCIR.

- Run `terraform apply` again

---

## Part 5: Final Deployment

After pushing images to OCIR, and reruning `terraform apply` with the  `use_placeholder_images = false` the infrastructure will automatically use them:

1.  **Verify Deployment:**
    ```bash
    # Check deployment status
    terraform output deployment_summary

    # Get application URL
    terraform output application_url
    ```

2.  **Test the Application:**
    *   Navigate to the application URL from the output above
    *   Upload an image to test the complete pipeline
    *   Check container instance logs for successful OCI client initialization
    *   Check function logs for successful execution when images are uploaded
    *   Verify database collection creation and data storage

---

## Part 6: Using the Application

1.  **Access the Web Application:**
    *   Get the load balancer IP from Terraform output: `terraform output`
    *   Navigate to `http://<load-balancer-ip>`

2.  **Upload and Analyze Images:**
    *   Upload an image using the web interface
    *   The image will be stored in Object Storage
    *   An event will trigger the vision function
    *   AI Vision will analyze the image
    *   Results will be stored in the JSON database
    *   You can view the analysis results in the web app

---

## Architecture Details

### Database Authentication
The application uses Oracle Autonomous Database REST API authentication:
1. **ADMIN user credentials** for ORDS REST API access
2. **HTTP Basic Authentication** with username and password
3. **Resource Principal authentication** for OCI services
4. **Graceful degradation** (Object Storage continues working if database fails)

### Oracle 23ai REST API Integration
- **Uses REST API** for all database operations
- **Oracle ORDS** (Oracle REST Data Services) for database access
- **Simplified deployment** using REST API authentication
- **HTTP-based operations** for storing and retrieving analysis results

### Container Configuration
- **Web App**: Runs Flask application on port 5000
- **Function**: Serverless function triggered by Object Storage events
- **Both**: Use Resource Principal authentication for OCI services
- **ARM64 Compatibility**: Uses `--platform=linux/amd64` for Oracle Instant Client
- **Function Architecture**: Uses `GENERIC_X86` shape for x86_64 compatibility

### Security Features
- Resource Principals authentication for OCI services
- REST API authentication for database access
- Minimal IAM permissions (least privilege)
- Private subnets for container instances
- Load balancer health checks

---

## Troubleshooting

### REST API Specific Issues
*   **HTTP 401 Unauthorized**: Incorrect database username/password for REST API calls
    - **Solution**: Verify ADMIN password matches Terraform configuration and ORDS endpoint URL

*   **AI Vision 404 Error**: Function cannot access AI Vision service
    - **Solution**: Use `resource.type = 'fnfunc'` in Dynamic Group matching rule instead of `resource.type = 'function'`

*   **Collection Not Found**: REST API returns 404 for missing collection
    - **Solution**: Collection is created automatically on first document insert via REST API

### Database Connection Issues

* **HTTP 401 Unauthorized**: Incorrect database username/password. Verify ADMIN password matches Terraform configuration.
*  **HTTP 404 Not Found**: REST API endpoint URL is incorrect. Check database ORDS URL in OCI Console.
*  **Connection timeout**: Database REST endpoint not accessible. Verify database is running and ORDS is enabled.

### Container/Function Issues
*   **`requests.exceptions.ConnectionError`**: Database REST API not accessible. Check DB_BASE_URL configuration.
*   **`Container failed to initialize`**: Check container logs for Python errors, missing dependencies, or Docker build issues.
*   **Function timeout**: Increase function memory allocation or check for infinite loops in code.
*   **`Function's image architecture 'x86' is incompatible with 'GENERIC_ARM'`**: Use `GENERIC_X86` function shape in Terraform.

### Template and Serialization Issues
*   **`Could not build url for endpoint 'view_result' with values ['json_filename']`**: URL parameter mismatch
    - **Solution**: Use consistent parameter names between routes and templates
*   **`Object of type JsonId is not JSON serializable`**: Oracle JsonId objects can't be serialized
    - **Solution**: Use `json.loads(json.dumps(data, default=str))` to clean data

### Infrastructure Issues
*   **Load balancer shows "No backends"**: Container instance failed to start. Check container logs.
*   **IAM authentication failures**: Verify Dynamic Groups and IAM policies are created in the correct Identity Domain.
*   **Terraform bucket deletion error**: Manually empty Object Storage bucket before `terraform destroy`.

### ARM64 Compatibility
*   **Oracle Instant Client package not found**: Always use `--platform=linux/amd64` when building containers.
*   **Package architecture mismatch**: Update Dockerfile to use correct Oracle Linux 9 packages.

### Testing Steps
1.  **Check container logs**: Look for database connection success messages
2.  **Test Object Storage**: Upload should work even if database fails
3.  **Test complete pipeline**: Upload image → check function logs → verify database storage
4.  **Check load balancer health**: Ensure backend shows as healthy
5.  **Test end-to-end functionality**:
    - Upload an image via web interface
    - Wait for "View Analysis" button to appear
    - Click "View Analysis" to see JSON results
    - Test delete functionality (removes from both storage and database)
    - Verify deletion with `soda get vision_results -all` in database

### Application Features
**Complete end-to-end image analysis pipeline**:
- ✅ **Image Upload**: Web interface for uploading images to Object Storage
- ✅ **Event-Driven Processing**: Automatic function trigger on image upload
- ✅ **AI Vision Analysis**: Object detection using OCI AI Vision service
- ✅ **JSON Database Storage**: Results stored in Oracle 23ai Autonomous JSON Database
- ✅ **Results Viewing**: Web interface to view detailed analysis results
- ✅ **File Management**: Delete images and their analysis results from both storage and database
- ✅ **Real-time Updates**: Status indicators show when analysis is complete
- ✅ **Error Handling**: Graceful degradation and comprehensive error messages

---

## Clean Up

To destroy all resources:
```bash
# First, empty the Object Storage bucket manually in OCI Console
terraform destroy -auto-approve
```

**Important**: You must manually delete all objects from the Object Storage bucket before running `terraform destroy`, or the operation will fail.

## Legal Notice

This project is for educational/demonstration purposes. When deploying:

- Review Oracle Cloud pricing and terms of service
- Ensure compliance with applicable data protection regulations
- This code is provided as-is without warranty

## Third-Party Licenses

This project uses the following open source packages:

- Oracle Cloud Infrastructure Python SDK: UPL-1.0 / Apache 2.0
- Flask: BSD-3-Clause
- Requests: Apache 2.0
- Other Python dependencies: Various permissive licenses (see requirements.txt)
