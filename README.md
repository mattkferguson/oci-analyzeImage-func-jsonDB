# OCI Event-Driven Image Analysis Application (with Autonomous JSON DB)

This project implements a full, event-driven pipeline on OCI. A web application uploads an image to a bucket, which triggers an OCI Function to perform object detection using the AI Vision service. The results are stored as documents in an **Oracle Autonomous JSON Database** and displayed in the web app.

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
            *   **Matching Rule:** `ALL {resource.type = 'function', resource.compartment.id = '<YOUR_COMPARTMENT_OCID>'}`
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
    
    **Important:** This first deployment will create the Autonomous Database but the applications will fail to connect because wallet files are not yet configured.

---

## Part 3: Configure Database Wallet (CRITICAL STEP)

After the initial Terraform deployment, you must download and configure the database wallet files.

1.  **Download Database Wallet:**
    *   In the OCI Console, navigate to **Oracle Database** → **Autonomous JSON Database**.
    *   Find your database (`visionjsondb`) and click on it.
    *   Click **"DB Connection"**.
    *   Click **"Download Wallet"**.
    *   Select **"Instance Wallet"**.
    *   Leave wallet password blank for auto-login wallet.
    *   Download the `wallet.zip` file.

2.  **Configure Wallet Files:**
    *   Extract `wallet.zip` to a temporary directory.
    *   Copy ALL extracted files to the `config/` directory in your project:
    ```bash
    # Extract wallet.zip and copy files
    unzip wallet.zip -d /tmp/wallet
    cp /tmp/wallet/* ./config/
    ```
    *   Ensure these files are present in `config/`:
        - `tnsnames.ora`
        - `sqlnet.ora`  
        - `cwallet.sso`
        - `ewallet.p12`
        - `ewallet.pem`
        - `keystore.jks`
        - `truststore.jks`
        - `ojdbc.properties`

3.  **Enable Database Resource Principals (Optional):**
    *   Connect to your Autonomous Database using SQL Developer or similar tool.
    *   Run: `EXEC DBMS_CLOUD_ADMIN.ENABLE_RESOURCE_PRINCIPAL();`
    *   This enables Resource Principals authentication (though the app will primarily use wallet-based authentication).

---

## Part 4: Build and Push Container Images

**IMPORTANT:** All container builds must use `--platform=linux/amd64` for Oracle Instant Client compatibility.

1.  **Log in to OCIR:**
    ```bash
    # Get your auth token from OCI Console -> User Settings -> Auth Tokens
    echo '<your-auth-token>' | podman login <region-key>.ocir.io --username '<tenancy-namespace>/<username>' --password-stdin
    ```

2.  **Build and Push the Web App Image:**
    ```bash
    # From the project root directory
    podman build --platform=linux/amd64 -t oci-image-upload-app-ajd:latest -f Dockerfile .
    podman tag oci-image-upload-app-ajd:latest <region-key>.ocir.io/<tenancy-namespace>/oci-image-upload-app-ajd:latest
    podman push <region-key>.ocir.io/<tenancy-namespace>/oci-image-upload-app-ajd:latest
    ```

3.  **Build and Push the Function Image:**
    ```bash
    # From the project root directory  
    podman build --platform=linux/amd64 -t vision-analyzer-func-ajd:latest -f vision_function/Dockerfile .
    podman tag vision-analyzer-func-ajd:latest <region-key>.ocir.io/<tenancy-namespace>/vision-analyzer-func-ajd:latest
    podman push <region-key>.ocir.io/<tenancy-namespace>/vision-analyzer-func-ajd:latest
    ```

---

## Part 5: Final Deployment

After configuring wallet files and pushing images, update your infrastructure:

1.  **Apply Terraform Again:**
    ```bash
    terraform apply -auto-approve
    ```
    This will update the container instances and function with the new images containing wallet files.

2.  **Verify Deployment:**
    *   Check container instance logs for: `"Successfully connected using Wallet with ADMIN user and password"`
    *   Check function logs for successful execution when images are uploaded.

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
The application uses Oracle Autonomous Database wallet-based authentication with fallback approaches:
1. **Auto-login wallet** (primary method)
2. **ADMIN user with password** (from Terraform-generated credentials)
3. **Resource Principals** (fallback)
4. **Graceful degradation** (Object Storage continues working if database fails)

### Oracle 23ai SODA Compatibility
- **Uses thick mode** (`oracledb.init_oracle_client()`) for SODA compatibility with Oracle 23ai
- **Oracle Instant Client 23ai** for latest database features
- **SODA collections** with string-based keys for 23ai compatibility
- **Transaction management** with explicit commit/rollback for reliable data operations

### Container Configuration
- **Web App**: Runs Flask application on port 5000
- **Function**: Serverless function triggered by Object Storage events
- **Both**: Include wallet files and Oracle environment variables
- **ARM64 Compatibility**: Uses `--platform=linux/amd64` for Oracle Instant Client
- **Function Architecture**: Uses `GENERIC_X86` shape for x86_64 compatibility

### Security Features
- Resource Principals authentication for OCI services
- Wallet-based database authentication
- Minimal IAM permissions (least privilege)
- Private subnets for container instances
- Load balancer health checks

---

## Lessons Learned

### Critical Oracle 23ai SODA Issues
1. **ORA-61754 Error**: "Using JSON type collections on Oracle Database release 23c or later requires a SODA driver for Oracle Database release 23c or later"
   - **Solution**: Keep Oracle 23ai and use thick mode with `oracledb==2.4.1`
   - **Alternative**: Could downgrade to Oracle 19c, but 23ai offers better JSON features

2. **SODA Document Deletion**: `SodaDocument.remove()` method doesn't exist
   - **Solution**: Use `collection.find().key(doc.key).remove()` instead
   - **Important**: Always call `connection.commit()` after SODA operations

3. **Template Variable Mismatch**: Flask templates expecting different parameter names than view functions
   - **Solution**: Ensure consistent naming between `render_template()` calls and template variables

### Terraform Infrastructure Challenges
1. **Password Requirements**: Autonomous Database requires specific password complexity
   - **Solution**: Use `random_password` resource with proper constraints (min_numeric, min_upper, etc.)

2. **Function Architecture Mismatch**: ARM vs x86_64 compatibility issues
   - **Solution**: Use `GENERIC_X86` function shape and `--platform=linux/amd64` for all builds

3. **Load Balancer Backend Dependencies**: Backend creation fails when container IP changes
   - **Solution**: Terraform automatically handles replacement when container instances are recreated

### Development Workflow Insights
1. **Wallet Configuration**: Critical step often missed in deployment guides
   - **Must download wallet after first Terraform apply**
   - **All files must be copied to config/ directory**
   - **Auto-login wallet (no password) works best**

2. **JSON Serialization**: Oracle JsonId objects not serializable by Flask
   - **Solution**: Use `json.loads(json.dumps(data, default=str))` to clean data

3. **Function vs Web App Consistency**: Both need identical database connection logic
   - **Keep connection code synchronized between components**
   - **Use same Oracle client version and configuration**

### Oracle Database Best Practices
1. **Thick Mode for 23ai**: Required for SODA operations on Oracle 23ai
2. **Explicit Transactions**: Always commit SODA operations explicitly
3. **Error Handling**: Implement multiple connection fallback methods
4. **Collection Metadata**: Use string keys for 23ai compatibility

---

## Troubleshooting

### Oracle 23ai Specific Issues
*   **`ORA-61754: Using JSON type collections...`**: SODA driver incompatible with 23ai
    - **Solution**: Use thick mode: `oracledb.init_oracle_client()` and `oracledb==2.4.1`
    - **Alternative**: Downgrade database to 19c if thick mode isn't suitable

*   **`'SodaDocument' object has no attribute 'remove'`**: Incorrect SODA deletion method  
    - **Solution**: Use `collection.find().key(doc.key).remove()` instead of `doc.remove()`

*   **Documents not actually deleted**: Missing transaction commit
    - **Solution**: Always call `connection.commit()` after SODA operations

### Database Connection Issues
*   **`ORA-01017: invalid credential or not authorized`**: Database wallet files are missing or invalid. Re-download wallet from OCI Console.
*   **`ORA-28759: failure to open file`**: Wallet file permissions or path issues. Ensure files are copied to `config/` directory.
*   **`ORA-12154: TNS:could not resolve the connect identifier specified`**: TNS configuration issue. Verify `tnsnames.ora` and `sqlnet.ora` are present.

### Container/Function Issues
*   **`TypeError: connect() got an unexpected keyword argument 'signer'`**: Old `oracledb` library version. Ensure `requirements.txt` specifies `oracledb==2.4.1`.
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