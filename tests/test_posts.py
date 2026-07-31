"""
Tests for post-related commands: post, delete, get, search
"""

import json
import pytest
import vcr
from unittest.mock import patch, MagicMock
from pathlib import Path

# Add scripts to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from x_client import get_client, get_my_user_id, cmd_post, cmd_delete, cmd_get, cmd_search, cmd_thread, cmd_upload_media, ReplyPolicyError, XWingError


# VCR configuration for recording/replaying HTTP requests
vcr_cassette_dir = Path(__file__).parent / "cassettes"


class TestPostCommand:
    """Tests for the 'post' command."""
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock X API client."""
        with patch('x_client.get_client') as mock_get_client:
            client = MagicMock()
            mock_get_client.return_value = client
            
            # Mock successful post creation
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.id = "1234567890"
            mock_response.data.text = "Test post content"
            client.posts.create.return_value = mock_response
            
            yield client, mock_get_client
    
    def test_post_creates_post(self, mock_client, capsys):
        """Test that post command creates a post with the given text."""
        client, mock_get_client = mock_client
        
        # Create args mock - use spec to avoid MagicMock auto-creating attributes
        args = MagicMock(spec=['text', 'json', 'media'])
        args.text = "Hello, World!"
        args.json = False
        args.media = None
        
        cmd_post(args)
        
        # Verify client was called with correct body
        client.posts.create.assert_called_once_with(body={"text": "Hello, World!"})
        
        # Check output
        captured = capsys.readouterr()
        assert "Post created successfully" in captured.out
        assert "1234567890" in captured.out
    
    def test_post_with_json_flag(self, mock_client, capsys):
        """Test that post command with --json outputs JSON."""
        client, mock_get_client = mock_client
        
        args = MagicMock()
        args.text = "Test post"
        args.json = True
        
        cmd_post(args)
        
        captured = capsys.readouterr()
        assert "Post created successfully" in captured.out

    def test_post_with_reply_target(self, mock_client, capsys):
        """Test that post command can reply to a specific post."""
        client, mock_get_client = mock_client

        args = MagicMock(spec=['text', 'json', 'media', 'reply_to', 'quote'])
        args.text = "Replying here"
        args.json = False
        args.media = None
        args.reply_to = "1234567890"
        args.quote = None

        cmd_post(args)

        client.posts.create.assert_called_once_with(
            body={
                "text": "Replying here",
                "reply": {"in_reply_to_tweet_id": "1234567890"},
            }
        )

    def test_post_with_quote_target(self, mock_client, capsys):
        """Test that post command can quote a specific post."""
        client, mock_get_client = mock_client

        args = MagicMock(spec=['text', 'json', 'media', 'reply_to', 'quote'])
        args.text = "Quoting this"
        args.json = False
        args.media = None
        args.reply_to = None
        args.quote = "1234567890"

        cmd_post(args)

        client.posts.create.assert_called_once_with(
            body={
                "text": "Quoting this",
                "quote_tweet_id": "1234567890",
            }
        )


class TestDeleteCommand:
    """Tests for the 'delete' command."""
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock X API client."""
        with patch('x_client.get_client') as mock_get_client:
            client = MagicMock()
            mock_get_client.return_value = client
            
            # Mock successful deletion
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.deleted = True
            client.posts.delete.return_value = mock_response
            
            yield client, mock_get_client
    
    def test_delete_removes_post(self, mock_client, capsys):
        """Test that delete command removes a post."""
        client, mock_get_client = mock_client
        
        args = MagicMock()
        args.id = "1234567890"
        args.json = False
        
        cmd_delete(args)
        
        client.posts.delete.assert_called_once_with(id="1234567890")
        
        captured = capsys.readouterr()
        assert "deleted successfully" in captured.out


class TestGetCommand:
    """Tests for the 'get' command."""
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock X API client."""
        with patch('x_client.get_client') as mock_get_client:
            client = MagicMock()
            mock_get_client.return_value = client
            
            # Mock post retrieval
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.id = "1234567890"
            mock_response.data.text = "This is a test post"
            mock_response.data.author_id = "987654321"
            mock_response.data.created_at = "2024-01-01T12:00:00Z"
            client.posts.get_by_id.return_value = mock_response
            
            yield client, mock_get_client
    
    def test_get_retrieves_post(self, mock_client, capsys):
        """Test that get command retrieves a post by ID."""
        client, mock_get_client = mock_client
        
        args = MagicMock()
        args.id = "1234567890"
        args.json = False
        
        cmd_get(args)
        
        captured = capsys.readouterr()
        assert "1234567890" in captured.out
        assert "This is a test post" in captured.out


class TestSearchCommand:
    """Tests for the 'search' command."""
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock X API client."""
        with patch('x_client.get_client') as mock_get_client:
            client = MagicMock()
            mock_get_client.return_value = client
            
            # Mock search results
            mock_page = MagicMock()
            mock_post1 = MagicMock()
            mock_post1.id = "111"
            mock_post1.text = "First result about Python"
            mock_post2 = MagicMock()
            mock_post2.id = "222"
            mock_post2.text = "Second result about Python"
            mock_page.data = [mock_post1, mock_post2]
            
            # Make the iterator return one page
            client.posts.search_recent.return_value = iter([mock_page])
            
            yield client, mock_get_client
    
    def test_search_finds_posts(self, mock_client, capsys):
        """Test that search command finds posts matching query."""
        client, mock_get_client = mock_client
        
        args = MagicMock()
        args.query = "Python"
        args.limit = 10
        args.json = False
        
        cmd_search(args)
        
        captured = capsys.readouterr()
        assert "Found 2 posts" in captured.out
        assert "Python" in captured.out


class TestGetClient:
    """Tests for OAuth 2.0 client initialization."""
    
    def test_get_client_with_valid_oauth2_credentials(self):
        """Test that get_client works with valid OAuth 2.0 credentials."""
        with patch.dict('os.environ', {
            'X_OAUTH2_CLIENT_ID': 'test_client_id',
            'X_OAUTH2_CLIENT_SECRET': 'test_client_secret',
            'X_OAUTH2_ACCESS_TOKEN': 'test_access_token',
        }, clear=True):
            with patch('x_client.Client') as mock_client:
                get_client()
                # OAuth 2.0 uses access_token parameter (X SDK convention)
                mock_client.assert_called_once_with(access_token='test_access_token')
    
    def test_get_client_app_only_with_client_credentials(self):
        """Test that get_client uses client credentials for app-only auth when no access token."""
        with patch.dict('os.environ', {
            'X_OAUTH2_CLIENT_ID': 'test_client_id',
            'X_OAUTH2_CLIENT_SECRET': 'test_client_secret',
            # Explicitly ensure no access token is set
            'X_OAUTH2_ACCESS_TOKEN': '',
            'X_ACCESS_TOKEN': '',
        }, clear=True):
            with patch('x_client.Client') as mock_client:
                get_client(use_app_only=True)
                # App-only auth uses client_id and client_secret
                mock_client.assert_called_once_with(
                    client_id='test_client_id',
                    client_secret='test_client_secret'
                )
    
    def test_get_client_missing_credentials(self, capsys):
        """Test that get_client raises XWingError when OAuth 2.0 credentials are missing."""
        with patch.dict('os.environ', {}, clear=True):
            with pytest.raises(XWingError):
                get_client()
    
    def test_get_client_missing_access_token_for_user_context(self):
        """Test that get_client raises XWingError when access token missing for user-context ops."""
        with patch.dict('os.environ', {
            'X_OAUTH2_CLIENT_ID': 'test_client_id',
            'X_OAUTH2_CLIENT_SECRET': 'test_client_secret',
            # Explicitly unset access tokens
            'X_OAUTH2_ACCESS_TOKEN': '',
            'X_ACCESS_TOKEN': '',
        }, clear=True):
            with pytest.raises(XWingError):
                get_client(use_app_only=False)


class TestGetMyUserId:
    """Tests for getting authenticated user ID."""
    
    def test_get_my_user_id_success(self):
        """Test successful retrieval of user ID."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = MagicMock()
        mock_response.data.id = "1234567890"
        mock_client.users.get_me.return_value = mock_response
        
        result = get_my_user_id(mock_client)
        assert result == "1234567890"
    
    def test_get_my_user_id_failure(self):
        """Test failure when user ID cannot be retrieved."""
        mock_client = MagicMock()
        mock_client.users.get_me.return_value = None
        
        with pytest.raises(ValueError, match="Could not get authenticated user ID"):
            get_my_user_id(mock_client)


class TestThreadCommand:
    """Tests for the 'thread' command."""
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock X API client."""
        with patch('x_client.get_client') as mock_get_client:
            client = MagicMock()
            mock_get_client.return_value = client
            
            # Mock successful post creation for thread with sequential IDs
            post_counter = [0]  # Use list for mutable counter in closure
            
            def create_side_effect(body):
                mock_response = MagicMock()
                mock_response.data = MagicMock()
                # Simulate sequential post IDs starting from post_0
                mock_response.data.id = f"post_{post_counter[0]}"
                post_counter[0] += 1
                return mock_response
            
            client.posts.create.side_effect = create_side_effect
            
            yield client, mock_get_client
    
    def test_thread_creates_multiple_posts(self, mock_client, capsys):
        """Test that thread command creates multiple linked posts."""
        client, mock_get_client = mock_client
        
        args = MagicMock()
        args.text = ["First post", "Second post", "Third post"]
        args.json = False
        
        cmd_thread(args)
        
        # Verify 3 posts were created
        assert client.posts.create.call_count == 3
        
        # Verify posts were created with correct text
        calls = client.posts.create.call_args_list
        assert calls[0][1]['body']['text'] == "First post"
        assert calls[1][1]['body']['text'] == "Second post"
        assert calls[2][1]['body']['text'] == "Third post"
        
        # Verify reply links (second and third posts should reply to previous)
        # First post has no reply, second replies to post_0, third replies to post_1
        assert 'reply' not in calls[0][1]['body']
        assert calls[1][1]['body']['reply']['in_reply_to_tweet_id'] == "post_0"
        assert calls[2][1]['body']['reply']['in_reply_to_tweet_id'] == "post_1"
        
        captured = capsys.readouterr()
        assert "Thread created successfully with 3 posts" in captured.out

    def test_thread_reports_reply_policy_target_post_id(self, mock_client, capsys):
        """Test that reply-policy errors identify the target post ID."""
        client, mock_get_client = mock_client
        call_count = {"value": 0}

        def side_effect(_operation=None, **kwargs):
            if call_count["value"] == 0:
                call_count["value"] += 1
                mock_response = MagicMock()
                mock_response.data = MagicMock()
                mock_response.data.id = "post_0"
                return mock_response
            call_count["value"] += 1
            raise ReplyPolicyError("Reply rejected by X: the original post does not allow replies from this account.")

        with patch("x_client.run_auth_operation", side_effect=side_effect):
            args = MagicMock()
            args.text = ["First post", "Second post"]
            args.json = False

            with pytest.raises(SystemExit):
                cmd_thread(args)

        captured = capsys.readouterr()
        assert "Target post ID: post_0" in captured.out


class TestMediaUploadCommand:
    """Tests for the 'upload-media' command."""
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock X API client."""
        with patch('x_client.get_client') as mock_get_client:
            client = MagicMock()
            mock_get_client.return_value = client
            
            # Mock successful media upload
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.id = "media_123456789"
            client.media.upload.return_value = mock_response
            
            yield client, mock_get_client
    
    def test_upload_media_success(self, mock_client, capsys, tmp_path):
        """Test that upload-media command uploads a file."""
        client, mock_get_client = mock_client
        
        # Create a test file
        test_file = tmp_path / "test_image.jpg"
        test_file.write_bytes(b"fake image data")
        
        args = MagicMock()
        args.file = str(test_file)
        args.json = False
        
        cmd_upload_media(args)
        
        # Verify media.upload was called
        client.media.upload.assert_called_once()
        call_kwargs = client.media.upload.call_args[1]
        assert call_kwargs['media_category'] == "tweet_image"
        
        captured = capsys.readouterr()
        assert "Media uploaded successfully" in captured.out
        assert "media_123456789" in captured.out
    
    def test_upload_media_file_not_found(self, mock_client, capsys):
        """Test that upload-media handles missing files."""
        client, mock_get_client = mock_client
        
        args = MagicMock()
        args.file = "/nonexistent/path/image.jpg"
        
        with pytest.raises(SystemExit):
            cmd_upload_media(args)
    
    def test_upload_media_invalid_extension(self, mock_client, capsys, tmp_path):
        """Test that upload-media rejects invalid file types."""
        client, mock_get_client = mock_client
        
        # Create a test file with invalid extension
        test_file = tmp_path / "test_file.txt"
        test_file.write_bytes(b"fake data")
        
        args = MagicMock()
        args.file = str(test_file)
        
        with pytest.raises(SystemExit):
            cmd_upload_media(args)


class TestPostWithMedia:
    """Tests for posting with media attachment."""
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock X API client."""
        with patch('x_client.get_client') as mock_get_client:
            client = MagicMock()
            mock_get_client.return_value = client
            
            # Mock successful post creation
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.id = "post_with_media_123"
            client.posts.create.return_value = mock_response
            
            yield client, mock_get_client
    
    def test_post_with_media(self, mock_client, capsys):
        """Test that post command can include media."""
        client, mock_get_client = mock_client
        
        args = MagicMock()
        args.text = "Check out this image!"
        args.media = "media_123456789"
        args.json = False
        
        cmd_post(args)
        
        # Verify post was created with media
        client.posts.create.assert_called_once()
        call_kwargs = client.posts.create.call_args[1]
        assert call_kwargs['body']['text'] == "Check out this image!"
        assert call_kwargs['body']['media']['media_ids'] == ["media_123456789"]
        
        captured = capsys.readouterr()
        assert "Post created successfully" in captured.out
