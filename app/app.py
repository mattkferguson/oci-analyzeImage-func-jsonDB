import oci
import requests
import base64
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import os
from werkzeug.utils import secure_filename
import json
import traceback

app = Flask(__name__)
app.secret_key = b'_5#y2L"F4Q8z\n\xec]/'

# --- Configuration ---
BUCKET_NAME = "oci-image-analysis-bucket"
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Database REST API Configuration
DB_BASE_URL = "https://g4f1b0a16e960d1-visionjsondb.adb.ca-toronto-1.oraclecloudapps.com/ords/admin/soda/latest"
DB_COLLECTION = "IMAGE_ANALYSIS"
DB_USERNAME = "ADMIN"
DB_PASSWORD = os.environ.get('DB_PASSWORD', '0Racle123456')

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- OCI Clients ---
signer = None
object_storage_client = None
namespace = None

def init_oci_clients():
    """Initialize OCI clients for Object Storage."""
    global signer, object_storage_client, namespace
    try:
        print("Attempting to authenticate with OCI Resource Principals...")
        signer = oci.auth.signers.get_resource_principals_signer()
        client_config = {'region': signer.region}
        object_storage_client = oci.object_storage.ObjectStorageClient(config=client_config, signer=signer)
        namespace = object_storage_client.get_namespace().data
        print(f"Object Storage client initialized for namespace: {namespace}")
        return True
    except Exception as e:
        print(f"Failed to initialize OCI clients: {e}")
        return False

def get_analysis_results():
    """Get all image analysis results from database via REST API."""
    try:
        auth = (DB_USERNAME, DB_PASSWORD)
        headers = {'Content-Type': 'application/json'}
        
        response = requests.get(
            f"{DB_BASE_URL}/{DB_COLLECTION}",
            auth=auth,
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            results = []
            
            for item in data.get('items', []):
                doc_data = item.get('value', {})
                # Add document ID for reference
                doc_data['doc_id'] = item.get('id')
                results.append(doc_data)
            
            print(f"Retrieved {len(results)} analysis results from database")
            return results
        else:
            print(f"Failed to get analysis results: HTTP {response.status_code}")
            return []
            
    except Exception as e:
        print(f"Error getting analysis results: {e}")
        return []

def get_bucket_images():
    """Get all images from the Object Storage bucket."""
    try:
        if not object_storage_client or not namespace:
            print("Object Storage client not initialized")
            return []
        
        # List objects in bucket
        list_objects_response = object_storage_client.list_objects(
            namespace_name=namespace,
            bucket_name=BUCKET_NAME
        )
        
        images = []
        for obj in list_objects_response.data.objects:
            # Only include image files
            if any(obj.name.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']):
                images.append(obj.name)
        
        print(f"Found {len(images)} images in bucket")
        return images
        
    except Exception as e:
        print(f"Error listing bucket images: {e}")
        return []

def allowed_file(filename):
    """Check if file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    """Display the main page with upload form and results."""
    # Get all images from bucket
    images = get_bucket_images()
    
    # Get analysis results
    analysis_results = get_analysis_results()
    
    # Create results dict keyed by image name for template
    results = {}
    for result in analysis_results:
        image_name = result.get('image_name', '')
        if image_name:
            results[image_name] = result
    
    print(f"Showing {len(images)} images, {len(results)} with analysis results")
    return render_template('index.html', images=images, results=results)

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload to Object Storage."""
    if 'file' not in request.files:
        flash('No file part')
        return redirect(request.url)
    
    file = request.files['file']
    if file.filename == '':
        flash('No selected file')
        return redirect(request.url)
    
    if file and allowed_file(file.filename):
        try:
            filename = secure_filename(file.filename)
            
            # Upload directly to Object Storage
            if object_storage_client and namespace:
                object_storage_client.put_object(
                    namespace_name=namespace,
                    bucket_name=BUCKET_NAME,
                    object_name=filename,
                    put_object_body=file.stream
                )
                flash(f'File {filename} uploaded successfully! Analysis will appear shortly.')
                print(f"Uploaded {filename} to Object Storage bucket {BUCKET_NAME}")
            else:
                flash('Object Storage client not initialized')
                
        except Exception as e:
            print(f"Error uploading file: {e}")
            flash(f'Error uploading file: {str(e)}')
    else:
        flash('Invalid file type. Please upload PNG, JPG, JPEG, or GIF files.')
    
    return redirect(url_for('index'))

@app.route('/api/results')
def api_results():
    """API endpoint to get analysis results as JSON."""
    results = get_analysis_results()
    return jsonify(results)

@app.route('/view_result/<image_filename>')
def view_result(image_filename):
    """View analysis result for a specific image."""
    analysis_results = get_analysis_results()
    result = next((r for r in analysis_results if r.get('image_name') == image_filename), None)
    
    if result:
        # Format the JSON properly for display
        formatted_json = json.dumps(result, indent=2, ensure_ascii=False)
        print(f"DEBUG: Formatted JSON preview: {formatted_json[:100]}...")
        
        return render_template('result.html', filename=image_filename, data=result, formatted_json=formatted_json)
    else:
        flash(f"Analysis for {image_filename} not found in the database.", "warning")
        return redirect(url_for('index'))

def delete_analysis_by_filename(filename):
    """Delete analysis results from database by filename via REST API."""
    try:
        # First, get all documents to find the one with matching filename
        auth = (DB_USERNAME, DB_PASSWORD)
        headers = {'Content-Type': 'application/json'}
        
        response = requests.get(
            f"{DB_BASE_URL}/{DB_COLLECTION}",
            auth=auth,
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            documents_to_delete = []
            
            for item in data.get('items', []):
                doc_data = item.get('value', {})
                if doc_data.get('image_name') == filename:
                    documents_to_delete.append(item.get('id'))
                    print(f"Found analysis document to delete: {item.get('id')} for {filename}")
            
            # Delete each matching document
            deleted_count = 0
            for doc_id in documents_to_delete:
                delete_url = f"{DB_BASE_URL}/{DB_COLLECTION}/{doc_id}"
                delete_response = requests.delete(
                    delete_url,
                    auth=auth,
                    headers=headers,
                    timeout=30
                )
                
                if delete_response.status_code in [200, 204]:
                    deleted_count += 1
                    print(f"Successfully deleted analysis document {doc_id} for {filename}")
                else:
                    print(f"Failed to delete document {doc_id}: HTTP {delete_response.status_code}")
                    print(f"Response: {delete_response.text}")
            
            return deleted_count
        else:
            print(f"Failed to retrieve documents for deletion: HTTP {response.status_code}")
            return 0
            
    except Exception as e:
        print(f"Error deleting analysis for {filename}: {e}")
        return 0

@app.route('/delete_file/<filename>', methods=['POST'])
def delete_file(filename):
    """Delete a file from Object Storage and its analysis results."""
    storage_deleted = False
    db_deleted_count = 0
    
    try:
        # Delete from Object Storage first
        if object_storage_client and namespace:
            object_storage_client.delete_object(
                namespace_name=namespace,
                bucket_name=BUCKET_NAME,
                object_name=filename
            )
            storage_deleted = True
            print(f"Deleted {filename} from Object Storage")
        
        # Delete from database
        db_deleted_count = delete_analysis_by_filename(filename)
        
        # Provide appropriate feedback
        if storage_deleted and db_deleted_count > 0:
            flash(f'Successfully deleted {filename} and {db_deleted_count} analysis record(s)')
        elif storage_deleted and db_deleted_count == 0:
            flash(f'Successfully deleted {filename} from storage (no analysis records found)')
        elif not storage_deleted and db_deleted_count > 0:
            flash(f'Deleted {db_deleted_count} analysis record(s) for {filename} (storage deletion failed)')
        else:
            flash(f'Warning: Could not fully delete {filename} and its analysis records')
        
    except Exception as e:
        print(f"Error deleting {filename}: {e}")
        flash(f'Error deleting {filename}: {str(e)}')
    
    return redirect(url_for('index'))

@app.route('/debug')
def debug():
    """Debug information endpoint."""
    return jsonify({
        'oci_initialized': object_storage_client is not None,
        'namespace': namespace,
        'database_url': DB_BASE_URL,
        'bucket_name': BUCKET_NAME
    })

@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'oci_initialized': object_storage_client is not None,
        'database_url': DB_BASE_URL
    })

if __name__ == '__main__':
    print("Starting Flask application...")
    
    # Initialize OCI clients
    if init_oci_clients():
        print("OCI clients initialized successfully")
    else:
        print("WARNING: OCI clients not initialized. Upload functionality may not work.")
    
    # Test database connection
    test_results = get_analysis_results()
    print(f"Database connection test: Retrieved {len(test_results)} existing results")
    
    app.run(host='0.0.0.0', port=5000, debug=False)