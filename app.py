import os
from flask import Flask, request, send_file, jsonify
import pdfplumber
import docx
from werkzeug.utils import secure_filename
import google.generativeai as genai
from fpdf import FPDF
from firebase_admin import credentials, firestore, initialize_app

# Set your API key
os.environ["GOOGLE_API_KEY"] = "AIzaSyDcJXT-_qs1wOKFuDUOvwSbjSy_phRbiYc"
genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
model = genai.GenerativeModel("models/gemini-1.5-pro")

# Firebase initialization
cred = credentials.Certificate('../aiquizgenerator-b8817-firebase-adminsdk-bml1x-9a92c0b273.json')  # Replace with the path to your Firebase credentials
initialize_app(cred)
db = firestore.client()

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads/'
app.config['RESULTS_FOLDER'] = 'results/'
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'txt', 'docx'}

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

if not os.path.exists(app.config['RESULTS_FOLDER']):
    os.makedirs(app.config['RESULTS_FOLDER'])

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def extract_text_from_file(file_path):
    if file_path.endswith('.pdf'):
        try:
            with pdfplumber.open(file_path) as pdf:
                return "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())
        except Exception as e:
            print(f"Error extracting text from PDF: {e}")
            return None
    elif file_path.endswith('.docx'):
        doc = docx.Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs])
    elif file_path.endswith('.txt'):
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    return None


@app.route('/generate_explanation', methods=['POST'])
def generate_explanation():
    try:
        data = request.get_json()
        question = data.get("question")
        correct_answer = data.get("correctAnswer")

        if not question or not correct_answer:
            return jsonify({"error": "Missing question or correct answer"}), 400

        prompt = f"""
        Explain why the correct answer to the following question is '{correct_answer}':
        {question}
        Provide a concise yet detailed explanation in simple terms.
        """

        print(f"🔹 Prompt Sent to AI: {prompt}")

        # Create a model instance
        model = genai.GenerativeModel("gemini-1.5-pro")

        # Generate explanation using Gemini AI (correct method)
        response = model.generate_content(prompt)

        if response and hasattr(response, "text"):
            explanation = response.text.strip()
        else:
            explanation = "AI could not generate an explanation."

        print(f"✅ Explanation Generated: {explanation}")

        return jsonify({"explanation": explanation}), 200

    except Exception as e:
        print(f"❌ Error Generating Explanation: {e}")
        return jsonify({"error": str(e)}), 500

def Question_mcqs_generator(input_text, num_questions):
    prompt = f"""
    You are an AI assistant helping the user generate multiple-choice questions (MCQs) based on the following text:
    '{input_text}'
    Please generate 25 MCQs from the text with each question tagged as Easy, Medium, or Hard. Each question should have:
    - A clear question with the difficulty level indicated at the start (e.g., [Easy], [Medium], [Hard])
    - Four answer options (labeled A, B, C, D)
    - The correct answer clearly indicated
    Format:
    ## MCQ 1
    [Difficulty Level] Question: [question]
    A) [option A]
    B) [option B]
    C) [option C]
    D) [option D]
    Correct Answer: [correct option]
    """
    response = model.generate_content(prompt).text.strip()
    return response

def save_mcqs_to_file(mcqs):
    txt_filename = "generated_mcqs.txt"
    txt_filepath = os.path.join(app.config['RESULTS_FOLDER'], txt_filename)
    with open(txt_filepath, 'w', encoding='utf-8') as f:
        f.write(mcqs)
    
    pdf_filename = "generated_mcqs.pdf"
    pdf_filepath = os.path.join(app.config['RESULTS_FOLDER'], pdf_filename)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Arial", size=12)

    try:
        pdf.multi_cell(0, 10, mcqs)
        pdf.output(pdf_filepath)
        print(f"PDF saved successfully to {pdf_filepath}")
    except Exception as e:
        print(f"Error generating PDF: {e}")
        return txt_filepath, None

    return txt_filepath, pdf_filepath

@app.route('/generate', methods=['POST'])
def generate_mcqs():
    file = request.files.get('file')
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        text = extract_text_from_file(file_path)
        if text is None or text.strip() == "":
            return "Error extracting text from file or empty file", 500
    else:
        text = request.form.get('text')
        if not text or text.strip() == "":
            return "No text input provided", 400

    try:
        mcqs = Question_mcqs_generator(text, 25)  # Set to generate 25 questions
        txt_filepath, pdf_filepath = save_mcqs_to_file(mcqs)

        doc_ref = db.collection('mcq_results').add({
            'mcqs': mcqs,
            'num_questions': 25,
            'txt_file': txt_filepath,
            'pdf_file': pdf_filepath,
            'timestamp': firestore.SERVER_TIMESTAMP
        })

        return jsonify({"mcqs": mcqs, "txt_file": txt_filepath, "pdf_file": pdf_filepath}), 200
    except Exception as e:
        print(f"Error generating MCQs: {e}")
        return "Error generating MCQs", 500

@app.route('/download/<file_type>/<filename>', methods=['GET'])
def download_file(file_type, filename):
    folder = app.config['RESULTS_FOLDER']
    file_path = os.path.join(folder, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return "File not found", 404

@app.route('/chatbot', methods=['POST'])
def chatbot():
    try:
        data = request.get_json()
        question = data.get("question")
        correct_answer = data.get("correctAnswer")

        if not question:
            return jsonify({"error": "Missing question"}), 400

        # General purpose chat prompt if no answer given
        if not correct_answer or correct_answer == "N/A":
            prompt = f"Answer the following question in simple terms:\n{question}"
        else:
            prompt = f"Explain why the correct answer to the following question is '{correct_answer}':\n{question}\nProvide a clear explanation."

        response = model.generate_content(prompt)
        explanation = response.text.strip() if hasattr(response, "text") else "AI couldn't respond."

        return jsonify({"explanation": explanation}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500



if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0')
