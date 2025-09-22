"""Enhanced security and authentication module."""
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.config.settings import settings


class SecurityService:
    """Enhanced security service with session management."""
    
    def __init__(self):
        self.security = HTTPBasic()
        self.active_sessions: Dict[str, datetime] = {}
        self.failed_attempts: Dict[str, list] = {}
        self.max_attempts = 5
        self.lockout_duration = timedelta(minutes=15)
    
    def _is_rate_limited(self, identifier: str) -> bool:
        """Check if identifier is rate limited."""
        if identifier not in self.failed_attempts:
            return False
        
        # Clean old attempts
        cutoff_time = datetime.utcnow() - self.lockout_duration
        self.failed_attempts[identifier] = [
            attempt for attempt in self.failed_attempts[identifier]
            if attempt > cutoff_time
        ]
        
        return len(self.failed_attempts[identifier]) >= self.max_attempts
    
    def _record_failed_attempt(self, identifier: str):
        """Record a failed authentication attempt."""
        if identifier not in self.failed_attempts:
            self.failed_attempts[identifier] = []
        
        self.failed_attempts[identifier].append(datetime.utcnow())
    
    def _clear_failed_attempts(self, identifier: str):
        """Clear failed attempts for identifier."""
        self.failed_attempts.pop(identifier, None)
    
    def _validate_password_strength(self, password: str) -> bool:
        """Validate password strength."""
        if len(password) < 8:
            return False
        
        # Check for at least one digit, one letter
        has_digit = any(c.isdigit() for c in password)
        has_letter = any(c.isalpha() for c in password)
        
        return has_digit and has_letter
    
    def validate_credentials(self, credentials: HTTPBasicCredentials = Depends(HTTPBasic())):
        """Validate user credentials with rate limiting."""
        identifier = credentials.username
        
        # Check rate limiting
        if self._is_rate_limited(identifier):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed attempts. Please try again later.",
                headers={"WWW-Authenticate": "Basic"},
            )
        
        # Validate credentials
        is_username_correct = secrets.compare_digest(credentials.username, settings.api_username)
        is_password_correct = secrets.compare_digest(credentials.password, settings.api_password)
        
        if not (is_username_correct and is_password_correct):
            self._record_failed_attempt(identifier)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        
        # Clear failed attempts on successful login
        self._clear_failed_attempts(identifier)
        return credentials.username
    
    def get_auth_dependency(self):
        """Get authentication dependency based on settings."""
        if settings.auth_enabled:
            return self.validate_credentials
        else:
            # Return a no-op dependency when auth is disabled
            def no_auth():
                return "anonymous"
            return no_auth
    
    def create_session_token(self, username: str) -> str:
        """Create a simple session token."""
        timestamp = str(int(datetime.utcnow().timestamp()))
        data = f"{username}:{timestamp}:{settings.secret_key}"
        return hashlib.sha256(data.encode()).hexdigest()
    
    def validate_session_token(self, token: str) -> Optional[str]:
        """Validate a simple session token."""
        # For now, we'll rely on HTTP Basic Auth
        # This can be enhanced later with proper JWT
        return None
    
    def generate_csrf_token(self) -> str:
        """Generate a CSRF token."""
        return secrets.token_urlsafe(32)
    
    def validate_csrf_token(self, token: str, expected: str) -> bool:
        """Validate a CSRF token."""
        return secrets.compare_digest(token, expected)


# Global security service instance
security_service = SecurityService()