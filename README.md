# OCI Event-Driven Image Analysis Application (REST API Version)

This project implements a full, event-driven pipeline on OCI. A web application uploads an image to a bucket, which triggers an OCI Function to perform object detection using the AI Vision service. The results are stored as documents in an **Oracle Autonomous JSON Database** via REST API and displayed in the web app.

**Key Features:**
- **Wallet-free deployment**: Uses Oracle REST Data Services (ORDS) for simplified database access
- **Resource Principal authentication**: No credential management required
- **Event-driven architecture**: Automatic processing when images are uploaded
- **Complete data consistency**: Proper cleanup of both storage and database records

## Architecture Diagram
```
[User] -> [Web Browser] -> [Flask Web App (OCI Container Instance)]
   |
   v
[1. Upload Image] -> [OCI Object Storage (Uploads Bucket)]
   |
   v (Triggers Event)
[2. OCI Event Rule] -> [3. OCI Function (vision-analyzer-func-ajd)]
   |
   v (Calls Vision API)
[4. OCI AI Vision Service] -> [Returns JSON]
   |
   v (Saves Result)
[5. Autonomous JSON Database] <- [Web App reads from DB]
```

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

    # --- Permissions for the OCI Event service ---
    Allow service logging to use functions-family in compartment id <YOUR_COMPARTMENT_OCID>
    ```
    *(Replace `<DOMAIN_NAME>` with the name of your identity domain, e.g., `OracleIdentityCloudService`).*
    *(Replace `<YOUR_COMPARTMENT_OCID>` with the OCID of your compartment).*

---

## Part 2: Deploy Infrastructure

1.  **Initialize Terraform:**
    ```bash
    terraform init -upgrade
    ```

2.  **Apply Terraform (First Time):**
    ```bash
    terraform apply -auto-approve
    ```
    
    **Important:** This first deployment will create the Autonomous Database. The applications will connect via REST API using the ADMIN user credentials.

---

## Part 3: Configure Database REST API Access

The application uses Oracle REST Data Services (ORDS) to access the database using HTTP Basic Authentication.

1.  **Verify Database REST API Endpoint:**
    *   In the OCI Console, navigate to **Oracle Database** → **Autonomous JSON Database**.
    *   Find your database (`visionjsondb`) and click on it.
    *   Click **"Service Console"** then **"Development"**.
    *   Note the **"RESTful Services"** URL - this should be similar to:
        `https://[unique-id]-visionjsondb.adb.[region].oraclecloudapps.com/ords/`

2.  **Update Database Configuration:**
    *   The application uses the ADMIN user and password configured in Terraform.
    *   The REST API endpoint is currently hardcoded in the code - you may need to update it for your database instance.
    *   Look for `DB_BASE_URL` in the REST version files to match your database's ORDS endpoint.

---

## Part 4: Set Up Oracle Container Registry (OCIR) and Build Images

**IMPORTANT:** All container builds must use `--platform=linux/amd64` for Oracle Instant Client compatibility.

1.  **Create Container Registry Repositories:**
    *   In the OCI Console, navigate to **Developer Services** → **Container Registry**.
    *   Click **"Create Repository"** and create two repositories:
        *   **Repository Name:** `oci-image-upload-app-ajd` (for the web app)
        *   **Repository Name:** `vision-analyzer-func-ajd` (for the function)
        *   **Access:** Set to "Public" or "Private" as needed
    *   Note down your **tenancy namespace** (visible in the repository URLs)

2.  **Generate Auth Token:**
    *   In the OCI Console, go to **Profile** → **User Settings** → **Auth Tokens**
    *   Click **"Generate Token"** 
    *   **Description:** "OCIR Docker Login"
    *   Copy the generated token immediately (it won't be shown again)

3.  **Log in to OCIR:**
    ```bash
    # Replace with your values:
    # <region-key> = your region (e.g., ord for us-chicago-1, iad for us-ashburn-1)
    # <tenancy-namespace> = your tenancy namespace from step 1
    # <username> = your OCI username (e.g., matt.ferguson@oracle.com)
    # <auth-token> = the token from step 2
    
    echo '<your-auth-token>' | podman login <region-key>.ocir.io --username '<tenancy-namespace>/<username>' --password-stdin
    
    # Example:
    # echo 'A1B2C3...' | podman login ord.ocir.io --username 'idrjq5zs9qgw/matt.ferguson@oracle.com' --password-stdin
    ```

4.  **Build and Push the Web App Image:**
    ```bash
    # From the project root directory
    podman build --platform=linux/amd64 -t oci-image-upload-app-ajd:latest -f Dockerfile .
    podman tag oci-image-upload-app-ajd:latest <region-key>.ocir.io/<tenancy-namespace>/oci-image-upload-app-ajd:latest
    podman push <region-key>.ocir.io/<tenancy-namespace>/oci-image-upload-app-ajd:latest
    
    # Example:
    # podman tag oci-image-upload-app-ajd:latest ord.ocir.io/idrjq5zs9qgw/oci-image-upload-app-ajd:latest
    # podman push ord.ocir.io/idrjq5zs9qgw/oci-image-upload-app-ajd:latest
    ```

5.  **Build and Push the Function Image:**
    ```bash
    # From the project root directory  
    podman build --platform=linux/amd64 -t vision-analyzer-func-ajd:latest -f vision_function/Dockerfile .
    podman tag vision-analyzer-func-ajd:latest <region-key>.ocir.io/<tenancy-namespace>/vision-analyzer-func-ajd:latest
    podman push <region-key>.ocir.io/<tenancy-namespace>/vision-analyzer-func-ajd:latest
    
    # Example:
    # podman tag vision-analyzer-func-ajd:latest ord.ocir.io/idrjq5zs9qgw/vision-analyzer-func-ajd:latest
    # podman push ord.ocir.io/idrjq5zs9qgw/vision-analyzer-func-ajd:latest
    ```

6.  **Update terraform.tfvars with Your Image URLs:**
    ```bash
    # Update terraform.tfvars with your actual image URLs:
    app_image_url      = "<region-key>.ocir.io/<tenancy-namespace>/oci-image-upload-app-ajd:latest"
    function_image_url = "<region-key>.ocir.io/<tenancy-namespace>/vision-analyzer-func-ajd:latest"
    ```

---

## Part 5: Final Deployment

After pushing images to OCIR, update your infrastructure:

1.  **Apply Terraform Again:**
    ```bash
    terraform apply -auto-approve
    ```
    This will update the container instances and function with the new images.

2.  **Verify Deployment:**
    *   Check container instance logs for successful OCI client initialization
    *   Check function logs for successful execution when images are uploaded
    *   Test the REST API endpoints to ensure database connectivity

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

## Lessons Learned

### REST API Implementation Benefits
1. **Simplified Database Access**: Direct HTTP calls to Oracle REST Data Services (ORDS)
   - **No driver complexity**: Uses standard HTTP requests with `requests` library
   - **No connection management**: Stateless REST calls eliminate connection pooling issues

2. **Easier Deployment**: No Oracle Instant Client or wallet files required
   - **Lightweight containers**: Smaller Docker images without Oracle dependencies
   - **Cross-platform compatibility**: Works consistently across different architectures

3. **Better Error Handling**: Standard HTTP status codes for database operations
   - **Clear error messages**: HTTP responses provide detailed error information
   - **Timeout control**: Configurable request timeouts for database calls

### Terraform Infrastructure Challenges
1. **Password Requirements**: Autonomous Database requires specific password complexity
   - **Solution**: Use `random_password` resource with proper constraints (min_numeric, min_upper, etc.)

2. **Function Architecture Mismatch**: ARM vs x86_64 compatibility issues
   - **Solution**: Use `GENERIC_X86` function shape and `--platform=linux/amd64` for all builds

3. **Load Balancer Backend Dependencies**: Backend creation fails when container IP changes
   - **Solution**: Terraform automatically handles replacement when container instances are recreated

### Development Workflow Insights
1. **REST API Configuration**: Ensure correct database endpoint URL
   - **Check ORDS URL in database Service Console**
   - **Update DB_BASE_URL in both web app and function code**
   - **Verify ADMIN password matches Terraform configuration**

2. **HTTP Request Handling**: REST API responses need proper error handling
   - **Check HTTP status codes before processing responses**
   - **Implement timeouts for database requests**

3. **Function vs Web App Consistency**: Both need identical database REST API logic
   - **Keep REST API configuration synchronized between components**
   - **Use same request patterns and error handling**

### Oracle Database Best Practices
1. **REST API Usage**: Use ORDS REST API for simplified database access
2. **HTTP Error Handling**: Always check response status codes for REST calls
3. **Request Timeout**: Implement reasonable timeouts for HTTP requests
4. **Authentication**: Use ADMIN user credentials for HTTP Basic Authentication

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
*   **HTTP 401 Unauthorized**: Incorrect database username/password. Verify ADMIN password matches Terraform configuration.
*   **HTTP 404 Not Found**: REST API endpoint URL is incorrect. Check database ORDS URL in OCI Console.
*   **Connection timeout**: Database REST endpoint not accessible. Verify database is running and ORDS is enabled.

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