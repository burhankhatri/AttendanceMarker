"""
Speech listener service - processes audio for name/roll number detection.
Used as a fallback when browser-based Web Speech API is not available.
"""


class SpeechListener:
    def __init__(self, detection_terms):
        self.detection_terms = [t.lower() for t in detection_terms]
        self.running = False

    def check_text(self, text):
        """Check if transcribed text contains any detection terms."""
        text_lower = text.lower()
        for term in self.detection_terms:
            if term in text_lower:
                return True, term
        return False, None

    def stop(self):
        self.running = False
