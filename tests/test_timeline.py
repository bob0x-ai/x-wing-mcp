"""
Tests for timeline commands: timeline, user-posts, mentions
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from x_client import cmd_timeline, cmd_user_posts, cmd_mentions


def create_mock_post(post_id, text, author_id="12345"):
    """Helper to create a mock post object."""
    post = MagicMock()
    post.id = post_id
    post.text = text
    post.author_id = author_id
    post.created_at = "2024-01-01T12:00:00Z"
    return post


class TestTimelineCommand:
    """Tests for the 'timeline' command."""
    
    @pytest.fixture
    def mock_setup(self):
        """Set up mock client and user ID."""
        with patch('x_client.get_client') as mock_get_client, \
             patch('x_client.get_my_user_id') as mock_get_id:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_get_id.return_value = "1234567890"
            
            # Mock timeline results
            mock_posts = [
                create_mock_post("111", "First timeline post"),
                create_mock_post("222", "Second timeline post"),
                create_mock_post("333", "Third timeline post"),
            ]
            
            mock_page = MagicMock()
            mock_page.data = mock_posts
            client.users.get_timeline.return_value = iter([mock_page])
            
            yield client, mock_get_id
    
    def test_timeline_retrieves_posts(self, mock_setup, capsys):
        """Test that timeline command retrieves home timeline."""
        client, mock_get_id = mock_setup
        
        args = MagicMock()
        args.limit = 10
        args.json = False
        
        cmd_timeline(args)
        
        captured = capsys.readouterr()
        assert "Home Timeline" in captured.out
        assert "First timeline post" in captured.out
    
    def test_timeline_respects_limit(self, mock_setup):
        """Test that timeline command respects the limit parameter."""
        client, mock_get_id = mock_setup
        
        args = MagicMock()
        args.limit = 2
        args.json = False
        
        cmd_timeline(args)
        
        # Verify get_timeline was called with correct max_results
        client.users.get_timeline.assert_called_once()
        call_kwargs = client.users.get_timeline.call_args[1]
        assert call_kwargs['max_results'] == 2


class TestUserPostsCommand:
    """Tests for the 'user-posts' command."""
    
    @pytest.fixture
    def mock_setup(self):
        """Set up mock client."""
        with patch('x_client.get_client') as mock_get_client:
            
            client = MagicMock()
            mock_get_client.return_value = client
            
            # Mock user lookup by username
            mock_user_response = MagicMock()
            mock_user_response.data = MagicMock()
            mock_user_response.data.id = "987654321"
            mock_user_response.data.username = "testuser"
            client.users.get_by_username.return_value = mock_user_response
            
            # Mock user posts
            mock_posts = [
                create_mock_post("111", "User's first post", "987654321"),
                create_mock_post("222", "User's second post", "987654321"),
            ]
            
            mock_page = MagicMock()
            mock_page.data = mock_posts
            client.users.get_posts.return_value = iter([mock_page])
            
            yield client
    
    def test_user_posts_with_username(self, mock_setup, capsys):
        """Test that user-posts command works with username."""
        client = mock_setup
        
        args = MagicMock()
        args.user = "@testuser"
        args.limit = 10
        args.json = False
        
        cmd_user_posts(args)
        
        # Verify username lookup was called
        client.users.get_by_username.assert_called_once_with(username="testuser")
        
        captured = capsys.readouterr()
        assert "Resolved @testuser" in captured.out
        assert "User posts" in captured.out
    
    def test_user_posts_with_user_id(self, mock_setup, capsys):
        """Test that user-posts command works with user ID directly."""
        client = mock_setup
        
        args = MagicMock()
        args.user = "123456789"  # Numeric ID
        args.limit = 10
        args.json = False
        
        cmd_user_posts(args)
        
        # Username lookup should NOT be called for numeric ID
        client.users.get_by_username.assert_not_called()
        
        captured = capsys.readouterr()
        assert "User posts" in captured.out
    
    def test_user_posts_user_not_found(self, mock_setup, capsys):
        """Test that user-posts handles user not found."""
        client = mock_setup
        client.users.get_by_username.return_value = None
        
        args = MagicMock()
        args.user = "@nonexistent"
        args.limit = 10
        args.json = False
        
        cmd_user_posts(args)
        
        captured = capsys.readouterr()
        assert "not found" in captured.out


class TestMentionsCommand:
    """Tests for the 'mentions' command."""
    
    @pytest.fixture
    def mock_setup(self):
        """Set up mock client and user ID."""
        with patch('x_client.get_client') as mock_get_client, \
             patch('x_client.get_my_user_id') as mock_get_id:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_get_id.return_value = "1234567890"
            
            # Mock mentions
            mock_mentions = [
                create_mock_post("111", "@me Hello there!"),
                create_mock_post("222", "@me How are you?"),
            ]
            
            mock_page = MagicMock()
            mock_page.data = mock_mentions
            client.users.get_mentions.return_value = iter([mock_page])
            
            yield client, mock_get_id
    
    def test_mentions_retrieves_posts(self, mock_setup, capsys):
        """Test that mentions command retrieves mentions."""
        client, mock_get_id = mock_setup
        
        args = MagicMock()
        args.limit = 10
        args.json = False
        
        cmd_mentions(args)
        
        captured = capsys.readouterr()
        assert "Mentions" in captured.out
        assert "@me" in captured.out
    
    def test_mentions_uses_authenticated_user_id(self, mock_setup):
        """Test that mentions command uses authenticated user's ID."""
        client, mock_get_id = mock_setup
        
        args = MagicMock()
        args.limit = 10
        args.json = False
        
        cmd_mentions(args)
        
        # Verify get_mentions was called with the correct user ID
        client.users.get_mentions.assert_called_once()
        call_kwargs = client.users.get_mentions.call_args[1]
        assert call_kwargs['id'] == "1234567890"


class TestPagination:
    """Tests for pagination handling in timeline commands."""
    
    def test_timeline_handles_multiple_pages(self, capsys):
        """Test that timeline command handles multiple pages correctly."""
        with patch('x_client.get_client') as mock_get_client, \
             patch('x_client.get_my_user_id') as mock_get_id:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_get_id.return_value = "1234567890"
            
            # Create multiple pages
            page1 = MagicMock()
            page1.data = [create_mock_post("1", "Post 1")]
            
            page2 = MagicMock()
            page2.data = [create_mock_post("2", "Post 2")]
            
            client.users.get_timeline.return_value = iter([page1, page2])
            
            args = MagicMock()
            args.limit = 5
            args.json = False
            
            cmd_timeline(args)
            
            captured = capsys.readouterr()
            assert "Post 1" in captured.out
            assert "Post 2" in captured.out


class TestJSONOutput:
    """Tests for JSON output flag."""
    
    def test_timeline_json_output(self, capsys):
        """Test that --json flag outputs JSON data."""
        with patch('x_client.get_client') as mock_get_client, \
             patch('x_client.get_my_user_id') as mock_get_id:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_get_id.return_value = "1234567890"
            
            mock_post = create_mock_post("111", "Test post")
            mock_post.model_dump = lambda: {"id": "111", "text": "Test post"}
            
            mock_page = MagicMock()
            mock_page.data = [mock_post]
            client.users.get_timeline.return_value = iter([mock_page])
            
            args = MagicMock()
            args.limit = 10
            args.json = True
            
            cmd_timeline(args)
            
            captured = capsys.readouterr()
            # JSON output should contain the id field
            assert "111" in captured.out


class TestTimelineOAuth2:
    """Test OAuth 2.0 authentication in timeline commands."""
    
    def test_timeline_uses_oauth2_user_context(self):
        """Test that timeline uses OAuth 2.0 user-context auth."""
        with patch('x_client.get_client') as mock_get_client, \
             patch('x_client.get_my_user_id') as mock_get_id:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_get_id.return_value = "1234567890"
            
            mock_page = MagicMock()
            mock_page.data = [create_mock_post("1", "Test")]
            client.users.get_timeline.return_value = iter([mock_page])
            
            args = MagicMock()
            args.limit = 10
            args.json = False
            
            cmd_timeline(args)
            
            # Verify get_client was called without app-only auth.
            assert mock_get_client.call_count >= 1
            for call in mock_get_client.call_args_list:
                if call[1]:
                    assert call[1].get('use_app_only', False) is False
    
    def test_user_posts_uses_oauth2(self):
        """Test that user-posts uses OAuth 2.0 auth."""
        with patch('x_client.get_client') as mock_get_client:
            client = MagicMock()
            mock_get_client.return_value = client
            
            # Mock user lookup
            mock_user_response = MagicMock()
            mock_user_response.data = MagicMock()
            mock_user_response.data.id = "987654321"
            mock_user_response.data.username = "testuser"
            client.users.get_by_username.return_value = mock_user_response
            
            mock_page = MagicMock()
            mock_page.data = [create_mock_post("1", "Test", "987654321")]
            client.users.get_posts.return_value = iter([mock_page])
            
            args = MagicMock()
            args.user = "@testuser"
            args.limit = 10
            args.json = False
            
            cmd_user_posts(args)
            
            # Verify get_client was called for the lookup and post fetch operations.
            assert mock_get_client.call_count >= 1
