import sys
import requests
import webbrowser
import urllib.parse
import logging
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLineEdit, QPushButton, QListWidget, QListWidgetItem, 
                             QMessageBox, QLabel, QProgressBar, QFrame, QSplitter,
                             QTextEdit, QScrollArea, QGroupBox)
from PyQt5.QtCore import QTimer, Qt, QThread, pyqtSignal, QPropertyAnimation, QRect, QEasingCurve
from PyQt5.QtGui import QFont, QPalette, QColor, QPixmap, QPainter, QLinearGradient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
URL_DATA_ROLE = Qt.UserRole + 1
ABSTRACT_DATA_ROLE = Qt.UserRole + 2
SOURCE_DATA_ROLE = Qt.UserRole + 3
AUTHORS_DATA_ROLE = Qt.UserRole + 4
DOI_DATA_ROLE = Qt.UserRole + 5
REQUEST_TIMEOUT = 15  # seconds


class SearchWorker(QThread):
    """Worker thread for API searches to prevent UI blocking"""
    results_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)
    status_update = pyqtSignal(str)
    
    def __init__(self, query):
        super().__init__()
        self.query = query
    
    def run(self):
        try:
            papers = self.search_papers(self.query)
            self.results_ready.emit(papers)
        except Exception as e:
            logger.error(f"Search failed: {str(e)}")
            self.error_occurred.emit(f"Search failed: {str(e)}")
    
    def search_papers(self, query):
        """Search for academic papers using CrossRef and Semantic Scholar APIs"""
        results = []
        seen_dois = set()
        encoded_query = urllib.parse.quote_plus(query)

        def add_paper(paper):
            """Helper to validate and add papers, avoiding duplicates"""
            doi = paper.get("doi", "").lower()
            title = paper.get("title", "").lower()
            unique_id = doi if doi else title
            if unique_id and unique_id not in seen_dois and len(unique_id) > 3:
                seen_dois.add(unique_id)
                results.append(paper)
                return True
            return False

        # CrossRef API
        self.status_update.emit("Searching CrossRef database...")
        crossref_url = f"https://api.crossref.org/works?query={encoded_query}&rows=15&sort=relevance&order=desc"
        try:
            response = requests.get(crossref_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            crossref_data = response.json()
            
            logger.info(f"CrossRef returned {len(crossref_data.get('message', {}).get('items', []))} items")
            
            count = 0
            for item in crossref_data.get("message", {}).get("items", []):
                if count >= 5:
                    break
                    
                doi = item.get("DOI", "")
                title = item.get("title", ["No Title"])
                title = title[0] if isinstance(title, list) and title else "No Title"
                
                # Extract authors with better formatting
                authors = []
                for author in item.get("author", []):
                    if "family" in author:
                        name_parts = []
                        if "given" in author:
                            name_parts.append(author["given"])
                        name_parts.append(author["family"])
                        authors.append(" ".join(name_parts))
                
                # Get publication year
                pub_date = item.get("published-print", item.get("published-online", {}))
                year = ""
                if pub_date and "date-parts" in pub_date:
                    year = str(pub_date["date-parts"][0][0]) if pub_date["date-parts"][0] else ""
                
                paper = {
                    "title": title,
                    "author": ", ".join(authors) if authors else "Unknown Author",
                    "source": "CrossRef",
                    "doi": doi.lower() if doi else "",
                    "url": f"https://doi.org/{doi}" if doi else "#",
                    "abstract": item.get("abstract", "Abstract not available from CrossRef."),
                    "year": year,
                    "journal": item.get("container-title", [""])[0] if item.get("container-title") else "",
                    "citations": item.get("is-referenced-by-count", 0)
                }
                
                if add_paper(paper):
                    count += 1
                    
            logger.info(f"Added {count} papers from CrossRef")
                    
        except requests.RequestException as e:
            logger.error(f"CrossRef API Error: {e}")
            self.status_update.emit("CrossRef search failed, continuing with Semantic Scholar...")

        # Semantic Scholar API with better error handling
        self.status_update.emit("Searching Semantic Scholar database...")
        
        # Try multiple endpoints and parameters for better results
        sem_sch_endpoints = [
            f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded_query}&limit=15&fields=title,authors,url,externalIds,abstract,year,venue,citationCount,publicationTypes",
            f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded_query}&limit=10&fields=title,authors,url,abstract,year"
        ]
        
        for endpoint_idx, sem_sch_url in enumerate(sem_sch_endpoints):
            try:
                headers = {
                    'User-Agent': 'Academic Paper Search App (educational use)',
                    'Accept': 'application/json'
                }
                
                response = requests.get(sem_sch_url, timeout=REQUEST_TIMEOUT, headers=headers)
                logger.info(f"Semantic Scholar response status: {response.status_code}")
                
                if response.status_code == 429:  # Rate limited
                    logger.warning("Semantic Scholar rate limited")
                    self.status_update.emit("Semantic Scholar rate limited, trying alternative...")
                    continue
                    
                response.raise_for_status()
                sem_sch_data = response.json()
                
                data_items = sem_sch_data.get("data", [])
                logger.info(f"Semantic Scholar returned {len(data_items)} items")
                
                if not data_items:
                    logger.warning(f"Semantic Scholar returned empty data for endpoint {endpoint_idx}")
                    if endpoint_idx < len(sem_sch_endpoints) - 1:
                        continue
                    else:
                        self.status_update.emit("Semantic Scholar found no results")
                        break
                
                count = 0
                for item in data_items:
                    if count >= 5:
                        break
                        
                    doi = item.get("externalIds", {}).get("DOI", "") if item.get("externalIds") else ""
                    authors = [author.get("name", "") for author in item.get("authors", [])]
                    
                    paper = {
                        "title": item.get("title", "No Title"),
                        "author": ", ".join(filter(None, authors)) or "Unknown Author",
                        "source": "Semantic Scholar",
                        "doi": doi.lower() if doi else "",
                        "url": item.get("url", "#"),
                        "abstract": item.get("abstract", "No abstract available."),
                        "year": str(item.get("year", "")) if item.get("year") else "",
                        "journal": item.get("venue", ""),
                        "citations": item.get("citationCount", 0)
                    }
                    
                    if add_paper(paper):
                        count += 1
                        
                logger.info(f"Added {count} papers from Semantic Scholar")
                break  # Success, don't try other endpoints
                        
            except requests.RequestException as e:
                logger.error(f"Semantic Scholar API Error (endpoint {endpoint_idx}): {e}")
                if endpoint_idx == len(sem_sch_endpoints) - 1:
                    self.status_update.emit("Semantic Scholar search failed")

        logger.info(f"Total papers found: {len(results)}")
        return results


class AnimatedButton(QPushButton):
    """Custom animated button with hover effects"""
    def __init__(self, text):
        super().__init__(text)
        self.animation = QPropertyAnimation(self, b"geometry")
        self.animation.setDuration(200)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        
    def enterEvent(self, event):
        super().enterEvent(event)
        # Slight grow effect on hover
        current = self.geometry()
        new_rect = QRect(current.x()-2, current.y()-2, current.width()+4, current.height()+4)
        self.animation.setStartValue(current)
        self.animation.setEndValue(new_rect)
        self.animation.start()
        
    def leaveEvent(self, event):
        super().leaveEvent(event)
        # Return to original size
        current = self.geometry()
        new_rect = QRect(current.x()+2, current.y()+2, current.width()-4, current.height()-4)
        self.animation.setStartValue(current)
        self.animation.setEndValue(new_rect)
        self.animation.start()


class PaperDetailPanel(QFrame):
    """Interactive panel for displaying paper details"""
    def __init__(self):
        super().__init__()
        self.setFrameStyle(QFrame.StyledPanel)
        self.setMinimumWidth(350)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(15)
        
        # Header
        self.header_label = QLabel("Paper Preview")
        self.header_label.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        self.header_label.setFont(font)
        layout.addWidget(self.header_label)
        
        # Title
        self.title_label = QLabel("Select a paper to view details")
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-weight: bold; font-size: 16px; color: #2c3e50;")
        layout.addWidget(self.title_label)
        
        # Authors
        self.authors_label = QLabel("")
        self.authors_label.setWordWrap(True)
        self.authors_label.setStyleSheet("color: #7f8c8d; font-style: italic;")
        layout.addWidget(self.authors_label)
        
        # Metadata
        self.metadata_frame = QFrame()
        self.metadata_frame.setStyleSheet("background-color: #ecf0f1; border-radius: 8px; padding: 10px;")
        metadata_layout = QVBoxLayout(self.metadata_frame)
        
        self.source_label = QLabel("")
        self.year_label = QLabel("")
        self.journal_label = QLabel("")
        self.citations_label = QLabel("")
        self.doi_label = QLabel("")
        
        for label in [self.source_label, self.year_label, self.journal_label, self.citations_label, self.doi_label]:
            label.setStyleSheet("font-size: 12px; margin: 2px;")
            metadata_layout.addWidget(label)
        
        layout.addWidget(self.metadata_frame)
        
        # Abstract
        abstract_group = QGroupBox("Abstract")
        abstract_layout = QVBoxLayout(abstract_group)
        
        self.abstract_text = QTextEdit()
        self.abstract_text.setReadOnly(True)
        self.abstract_text.setMaximumHeight(200)
        self.abstract_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #bdc3c7;
                border-radius: 4px;
                background-color: white;
                font-size: 13px;
                line-height: 1.4;
            }
        """)
        abstract_layout.addWidget(self.abstract_text)
        layout.addWidget(abstract_group)
        
        # Action buttons
        button_layout = QHBoxLayout()
        self.open_button = AnimatedButton("Open Paper")
        self.copy_button = AnimatedButton("Copy Citation")
        
        self.open_button.clicked.connect(self.open_current_paper)
        self.copy_button.clicked.connect(self.copy_citation)
        
        button_layout.addWidget(self.open_button)
        button_layout.addWidget(self.copy_button)
        layout.addLayout(button_layout)
        
        layout.addStretch()
        self.setLayout(layout)
        
        self.current_paper_url = None
        self.current_paper_data = None
        
    def update_paper_details(self, paper_data):
        """Update the panel with paper details"""
        self.current_paper_data = paper_data
        
        if not paper_data:
            self.clear_details()
            return
            
        # Update title
        title = paper_data.get('title', 'No Title')
        self.title_label.setText(title)
        
        # Update authors
        authors = paper_data.get('author', 'Unknown Author')
        self.authors_label.setText(f"Authors: {authors}")
        
        # Update metadata
        source = paper_data.get('source', '')
        year = paper_data.get('year', '')
        journal = paper_data.get('journal', '')
        citations = paper_data.get('citations', 0)
        doi = paper_data.get('doi', '')
        
        self.source_label.setText(f"📊 Source: {source}")
        self.year_label.setText(f"📅 Year: {year}" if year else "📅 Year: Not available")
        self.journal_label.setText(f"📖 Journal: {journal}" if journal else "📖 Journal: Not available")
        self.citations_label.setText(f"📈 Citations: {citations}")
        self.doi_label.setText(f"🔗 DOI: {doi}" if doi else "🔗 DOI: Not available")
        
        # Update abstract
        abstract = paper_data.get('abstract', 'No abstract available.')
        self.abstract_text.setPlainText(abstract)
        
        # Store URL
        self.current_paper_url = paper_data.get('url', '#')
        
        # Enable/disable buttons
        self.open_button.setEnabled(self.current_paper_url and self.current_paper_url != '#')
        self.copy_button.setEnabled(True)
        
    def clear_details(self):
        """Clear all details from the panel"""
        self.title_label.setText("Select a paper to view details")
        self.authors_label.setText("")
        self.source_label.setText("")
        self.year_label.setText("")
        self.journal_label.setText("")
        self.citations_label.setText("")
        self.doi_label.setText("")
        self.abstract_text.setPlainText("")
        self.current_paper_url = None
        self.current_paper_data = None
        self.open_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        
    def open_current_paper(self):
        """Open the current paper in browser"""
        if self.current_paper_url and self.current_paper_url != '#':
            webbrowser.open(self.current_paper_url)
            
    def copy_citation(self):
        """Copy citation to clipboard"""
        if not self.current_paper_data:
            return
            
        # Create a simple citation
        title = self.current_paper_data.get('title', 'No Title')
        authors = self.current_paper_data.get('author', 'Unknown Author')
        year = self.current_paper_data.get('year', '')
        journal = self.current_paper_data.get('journal', '')
        
        citation = f"{authors} ({year}). {title}."
        if journal:
            citation += f" {journal}."
            
        clipboard = QApplication.clipboard()
        clipboard.setText(citation)
        
        # Show confirmation
        QMessageBox.information(self, "Citation Copied", "Citation has been copied to clipboard!")


class PaperSearchApp(QWidget):
    def __init__(self):
        super().__init__()
        self.search_worker = None
        self.initUI()
        self.setStyleSheet(self.get_stylesheet())

    def initUI(self):
        """Initialize the user interface"""
        self.setWindowTitle("🔬 Academic Paper Explorer")
        self.setGeometry(100, 100, 1400, 800)
        
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(25, 25, 25, 25)

        # Header section with gradient background
        header_frame = QFrame()
        header_frame.setObjectName("headerFrame")
        header_layout = QVBoxLayout(header_frame)
        
        title_label = QLabel("🔬 Academic Paper Explorer")
        title_label.setAlignment(Qt.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(24)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: white; margin: 20px;")
        
        subtitle_label = QLabel("Discover research papers from CrossRef and Semantic Scholar")
        subtitle_label.setAlignment(Qt.AlignCenter)
        subtitle_label.setStyleSheet("color: #ecf0f1; font-size: 14px; margin-bottom: 20px;")
        
        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)
        main_layout.addWidget(header_frame)

        # Search section
        search_frame = QFrame()
        search_frame.setObjectName("searchFrame")
        search_layout = QVBoxLayout(search_frame)
        
        # Search input layout
        input_layout = QHBoxLayout()
        
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 Enter research topic (e.g., 'machine learning', 'climate change', 'quantum computing')...")
        self.search_box.returnPressed.connect(self.perform_search)
        self.search_box.setMinimumHeight(50)
        input_layout.addWidget(self.search_box)

        self.search_button = AnimatedButton("🚀 Search")
        self.search_button.clicked.connect(self.perform_search)
        self.search_button.setMinimumSize(120, 50)
        input_layout.addWidget(self.search_button)
        
        search_layout.addLayout(input_layout)
        
        # Progress and status
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 10px;
                background-color: #ecf0f1;
                height: 20px;
            }
            QProgressBar::chunk {
                border-radius: 10px;
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #3498db, stop: 1 #2980b9);
            }
        """)
        search_layout.addWidget(self.progress_bar)
        
        main_layout.addWidget(search_frame)

        # Main content area with splitter
        content_splitter = QSplitter(Qt.Horizontal)
        
        # Left side - Results list
        results_frame = QFrame()
        results_layout = QVBoxLayout(results_frame)
        
        results_header = QLabel("📚 Search Results")
        results_font = QFont()
        results_font.setPointSize(14)
        results_font.setBold(True)
        results_header.setFont(results_font)
        results_layout.addWidget(results_header)

        self.results_list = QListWidget()
        self.results_list.setAlternatingRowColors(True)
        self.results_list.itemClicked.connect(self.on_paper_selected)
        self.results_list.itemSelectionChanged.connect(self.on_selection_changed)
        results_layout.addWidget(self.results_list)
        
        content_splitter.addWidget(results_frame)
        
        # Right side - Paper details
        self.detail_panel = PaperDetailPanel()
        content_splitter.addWidget(self.detail_panel)
        
        # Set splitter proportions
        content_splitter.setSizes([700, 400])
        main_layout.addWidget(content_splitter)

        # Status bar
        self.status_label = QLabel("💡 Enter a topic and click Search to discover academic papers")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #7f8c8d; font-style: italic; padding: 10px;")
        main_layout.addWidget(self.status_label)

        self.setLayout(main_layout)

    def get_stylesheet(self):
        """Return CSS stylesheet for the application"""
        return """
            QWidget {
                background-color: #f8f9fa;
                font-family: 'Segoe UI', 'Roboto', Arial, sans-serif;
            }
            
            #headerFrame {
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #667eea, stop: 1 #764ba2);
                border-radius: 15px;
                margin-bottom: 10px;
            }
            
            #searchFrame {
                background-color: white;
                border: 2px solid #e9ecef;
                border-radius: 15px;
                padding: 20px;
            }
            
            QLineEdit {
                padding: 15px 20px;
                border: 2px solid #dee2e6;
                border-radius: 25px;
                font-size: 15px;
                background-color: white;
                selection-background-color: #007bff;
            }
            
            QLineEdit:focus {
                border-color: #007bff;
                box-shadow: 0 0 0 3px rgba(0, 123, 255, 0.25);
            }
            
            QPushButton, AnimatedButton {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 #007bff, stop: 1 #0056b3);
                color: white;
                border: none;
                border-radius: 25px;
                font-size: 15px;
                font-weight: bold;
                padding: 15px 30px;
            }
            
            QPushButton:hover, AnimatedButton:hover {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 #0056b3, stop: 1 #004085);
                transform: translateY(-2px);
            }
            
            QPushButton:pressed, AnimatedButton:pressed {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 #004085, stop: 1 #002752);
            }
            
            QPushButton:disabled, AnimatedButton:disabled {
                background-color: #6c757d;
            }
            
            QListWidget {
                border: 2px solid #dee2e6;
                border-radius: 10px;
                background-color: white;
                selection-background-color: #e3f2fd;
                outline: none;
            }
            
            QListWidget::item {
                padding: 15px;
                border-bottom: 1px solid #f1f3f4;
                border-radius: 5px;
                margin: 2px;
            }
            
            QListWidget::item:hover {
                background-color: #f8f9ff;
                border: 1px solid #007bff;
            }
            
            QListWidget::item:selected {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 #e3f2fd, stop: 1 #bbdefb);
                color: #1565c0;
                border: 2px solid #2196f3;
            }
            
            QFrame {
                background-color: white;
                border: 1px solid #dee2e6;
                border-radius: 10px;
                padding: 15px;
            }
            
            QGroupBox {
                font-weight: bold;
                border: 2px solid #dee2e6;
                border-radius: 8px;
                margin-top: 1ex;
                padding-top: 10px;
            }
            
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
            
            QSplitter::handle {
                background-color: #dee2e6;
                width: 3px;
            }
            
            QSplitter::handle:hover {
                background-color: #007bff;
            }
        """

    def perform_search(self):
        """Initiate search for academic papers"""
        topic = self.search_box.text().strip()
        if not topic:
            QMessageBox.warning(self, "Input Required", "Please enter a search topic.")
            return

        # Clear previous results and details
        self.results_list.clear()
        self.detail_panel.clear_details()

        # Disable search while working
        self.search_button.setEnabled(False)
        self.search_box.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # Indeterminate progress
        self.status_label.setText("🔍 Searching academic databases...")

        # Start search in worker thread
        self.search_worker = SearchWorker(topic)
        self.search_worker.results_ready.connect(self.display_results)
        self.search_worker.error_occurred.connect(self.handle_search_error)
        self.search_worker.status_update.connect(self.update_status)
        self.search_worker.finished.connect(self.search_finished)
        self.search_worker.start()

    def update_status(self, message):
        """Update status message during search"""
        self.status_label.setText(f"🔍 {message}")

    def display_results(self, papers):
        """Display search results in the list widget"""
        self.results_list.clear()
        
        if not papers:
            self.status_label.setText("❌ No papers found. Try a different search term.")
            return

        # Add papers to list with better formatting
        for paper in papers:
            self.add_paper_item(paper)

        crossref_count = len([p for p in papers if p["source"] == "CrossRef"])
        scholar_count = len([p for p in papers if p["source"] == "Semantic Scholar"])
        
        self.status_label.setText(
            f"✅ Found {len(papers)} papers "
            f"({crossref_count} from CrossRef, {scholar_count} from Semantic Scholar)"
        )

    def add_paper_item(self, paper):
        """Add a paper item to the results list"""
        title = paper["title"]
        authors = paper["author"]
        source = paper["source"]
        year = paper.get("year", "")
        citations = paper.get("citations", 0)
        
        # Create formatted display text with emojis and better layout
        source_emoji = "📊" if source == "CrossRef" else "🎓"
        year_text = f" ({year})" if year else ""
        citation_text = f" • {citations} citations" if citations else ""
        
        item_text = f"{source_emoji} {title}{year_text}\n👥 {authors}{citation_text}\n📍 {source}"
        
        item = QListWidgetItem(item_text)
        
        # Store all paper data
        item.setData(URL_DATA_ROLE, paper["url"])
        item.setData(ABSTRACT_DATA_ROLE, paper.get("abstract", "No abstract available."))
        item.setData(SOURCE_DATA_ROLE, source)
        item.setData(AUTHORS_DATA_ROLE, authors)
        item.setData(DOI_DATA_ROLE, paper.get("doi", ""))
        
        # Store complete paper data for detail panel
        item.setData(Qt.UserRole + 10, paper)
        
        # Add visual styling based on source
        if source == "CrossRef":
            item.setBackground(QColor(255, 248, 240))  # Light orange
        else:
            item.setBackground(QColor(240, 248, 255))  # Light blue
            
        self.results_list.addItem(item)

    def on_paper_selected(self, item):
        """Handle paper selection"""
        paper_data = item.data(Qt.UserRole + 10)
        self.detail_panel.update_paper_details(paper_data)
        
    def on_selection_changed(self):
        """Handle selection change"""
        current_item = self.results_list.currentItem()
        if current_item:
            self.on_paper_selected(current_item)
        else:
            self.detail_panel.clear_details()

    def handle_search_error(self, error_message):
        """Handle search errors"""
        QMessageBox.critical(self, "Search Error", error_message)
        self.status_label.setText("❌ Search failed. Please try again.")

    def search_finished(self):
        """Re-enable UI after search completion"""
        self.search_button.setEnabled(True)
        self.search_box.setEnabled(True)
        self.progress_bar.setVisible(False)
        
        if self.search_worker:
            self.search_worker.deleteLater()
            self.search_worker = None


def main():
    """Main application entry point"""
    app = QApplication(sys.argv)
    app.setApplicationName("Academic Paper Explorer")
    app.setApplicationVersion("3.0")
    
    # Set application style
    app.setStyle('Fusion')
    
    window = PaperSearchApp()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
