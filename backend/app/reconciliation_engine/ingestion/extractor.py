import os
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import docx
except ImportError:
    docx = None


def get_file_extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def extract_file_metadata(file_path: Path, filename: str) -> List[Dict[str, Any]]:
    """
    Returns a list of available 'sheets' or 'tables' within the file.
    For CSV, it's just one table.
    For Excel, it's the sheet names.
    For PDF/Word, it attempts to find tables.
    """
    ext = get_file_extension(filename)
    
    if ext in ['.xlsx', '.xls']:
        try:
            xl = pd.ExcelFile(file_path)
            return [{"id": sheet, "name": sheet} for sheet in xl.sheet_names]
        except Exception as e:
            raise ValueError(f"Failed to read Excel file: {e}")
            
    elif ext in ['.csv', '.tsv', '.txt']:
        # For CSV/TSV, there is only one "sheet".
        return [{"id": "default", "name": "Main Data"}]
        
    elif ext == '.pdf':
        if pdfplumber is None:
            raise ImportError("pdfplumber is required to parse PDFs. Please install it.")
        tables = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    page_tables = page.find_tables()
                    for j, _ in enumerate(page_tables):
                        tables.append({
                            "id": f"page_{i+1}_table_{j+1}",
                            "name": f"Page {i+1} - Table {j+1}"
                        })
        except Exception as e:
            raise ValueError(f"Failed to parse PDF: {e}")
            
        if not tables:
            raise ValueError("No tables detected in PDF.")
        return tables
        
    elif ext in ['.docx', '.doc']:
        if docx is None:
            raise ImportError("python-docx is required to parse Word documents.")
        try:
            doc = docx.Document(file_path)
            tables = []
            for i, table in enumerate(doc.tables):
                # Basic check if it looks like a data table (has rows)
                if len(table.rows) > 0:
                    tables.append({
                        "id": f"table_{i+1}",
                        "name": f"Table {i+1} ({len(table.rows)} rows)"
                    })
            if not tables:
                raise ValueError("No data tables detected in Word document.")
            return tables
        except Exception as e:
            raise ValueError(f"Failed to parse Word document: {e}")
            
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def read_table_data(file_path: Path, filename: str, sheet_id: str) -> pd.DataFrame:
    """
    Reads the specific sheet/table from the file into a Pandas DataFrame.
    """
    ext = get_file_extension(filename)
    
    if ext in ['.xlsx', '.xls']:
        return pd.read_excel(file_path, sheet_name=sheet_id)
        
    elif ext in ['.csv', '.txt']:
        return pd.read_csv(file_path)
        
    elif ext == '.tsv':
        return pd.read_csv(file_path, sep='\t')
        
    elif ext == '.pdf':
        if pdfplumber is None:
            raise ImportError("pdfplumber is required")
            
        try:
            # sheet_id format: page_{i}_table_{j}
            parts = sheet_id.split('_')
            page_idx = int(parts[1]) - 1
            table_idx = int(parts[3]) - 1
            
            with pdfplumber.open(file_path) as pdf:
                page = pdf.pages[page_idx]
                tables = page.extract_tables()
                if table_idx < len(tables):
                    table_data = tables[table_idx]
                    if not table_data or len(table_data) < 2:
                        return pd.DataFrame() # empty or no headers
                    
                    # Assume first row is header
                    df = pd.DataFrame(table_data[1:], columns=table_data[0])
                    return df
                else:
                    raise ValueError(f"Table index {table_idx} out of range for page {page_idx+1}")
        except Exception as e:
            raise ValueError(f"Failed to extract PDF table: {e}")
            
    elif ext in ['.docx', '.doc']:
        if docx is None:
            raise ImportError("python-docx is required")
            
        try:
            parts = sheet_id.split('_')
            table_idx = int(parts[1]) - 1
            
            doc = docx.Document(file_path)
            if table_idx < len(doc.tables):
                table = doc.tables[table_idx]
                
                data = []
                for row in table.rows:
                    data.append([cell.text for cell in row.cells])
                    
                if not data or len(data) < 2:
                    return pd.DataFrame()
                    
                df = pd.DataFrame(data[1:], columns=data[0])
                return df
            else:
                raise ValueError(f"Table index {table_idx} out of range")
        except Exception as e:
            raise ValueError(f"Failed to extract Word table: {e}")
            
    else:
        raise ValueError(f"Unsupported file format: {ext}")
