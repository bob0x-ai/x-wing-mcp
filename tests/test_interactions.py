"""
Tests for interaction commands: like, unlike, repost, unrepost
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys
import os

sys.path.insert(0, str(Path(__file__).parent.parent))

from x_client import cmd_like, cmd_unlike, cmd_repost, cmd_unrepost

TEST_SCOPES = "tweet.read tweet.write like.write follows.write offline.access users.read"


class TestLikeCommand:
    """Tests for the 'like' command."""
    
    @pytest.fixture
    def mock_setup(self):
        """Set up mock client and user ID."""
        with patch.dict(os.environ, {
            "X_OAUTH2_SCOPES": TEST_SCOPES,
            "X_SCOPES": TEST_SCOPES,
        }), patch('x_client.get_client') as mock_get_client, \
             patch('x_client.get_my_user_id') as mock_get_id:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_get_id.return_value = "1234567890"
            
            # Mock successful like
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.liked = True
            client.users.like_post.return_value = mock_response
            
            yield client, mock_get_id
    
    def test_like_post_success(self, mock_setup, capsys):
        """Test that like command likes a post."""
        client, mock_get_id = mock_setup
        
        args = MagicMock()
        args.id = "987654321"
        args.json = False
        
        cmd_like(args)
        
        # Verify like was called with correct parameters
        client.users.like_post.assert_called_once()
        call_args = client.users.like_post.call_args
        assert call_args[1]['id'] == "1234567890"  # User ID
        assert call_args[1]['body']['tweet_id'] == "987654321"  # Post ID
        
        captured = capsys.readouterr()
        assert "liked successfully" in captured.out


class TestUnlikeCommand:
    """Tests for the 'unlike' command."""
    
    @pytest.fixture
    def mock_setup(self):
        """Set up mock client and user ID."""
        with patch.dict(os.environ, {
            "X_OAUTH2_SCOPES": TEST_SCOPES,
            "X_SCOPES": TEST_SCOPES,
        }), patch('x_client.get_client') as mock_get_client, \
             patch('x_client.get_my_user_id') as mock_get_id:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_get_id.return_value = "1234567890"
            
            # Mock successful unlike
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.liked = False
            client.users.unlike_post.return_value = mock_response
            
            yield client, mock_get_id
    
    def test_unlike_post_success(self, mock_setup, capsys):
        """Test that unlike command unlikes a post."""
        client, mock_get_id = mock_setup
        
        args = MagicMock()
        args.id = "987654321"
        args.json = False
        
        cmd_unlike(args)
        
        # Verify unlike was called with correct parameters
        client.users.unlike_post.assert_called_once_with(
            id="1234567890",
            tweet_id="987654321"
        )
        
        captured = capsys.readouterr()
        assert "unliked successfully" in captured.out


class TestRepostCommand:
    """Tests for the 'repost' command."""
    
    @pytest.fixture
    def mock_setup(self):
        """Set up mock client and user ID."""
        with patch.dict(os.environ, {
            "X_OAUTH2_SCOPES": TEST_SCOPES,
            "X_SCOPES": TEST_SCOPES,
        }), patch('x_client.get_client') as mock_get_client, \
             patch('x_client.get_my_user_id') as mock_get_id:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_get_id.return_value = "1234567890"
            
            # Mock successful repost
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.reposted = True
            client.users.repost_post.return_value = mock_response
            
            yield client, mock_get_id
    
    def test_repost_success(self, mock_setup, capsys):
        """Test that repost command reposts a post."""
        client, mock_get_id = mock_setup
        
        args = MagicMock()
        args.id = "987654321"
        args.json = False
        
        cmd_repost(args)
        
        # Verify repost was called with correct parameters
        client.users.repost_post.assert_called_once()
        call_args = client.users.repost_post.call_args
        assert call_args[1]['id'] == "1234567890"  # User ID
        assert call_args[1]['body']['tweet_id'] == "987654321"  # Post ID
        
        captured = capsys.readouterr()
        assert "reposted successfully" in captured.out


class TestUnrepostCommand:
    """Tests for the 'unrepost' command."""
    
    @pytest.fixture
    def mock_setup(self):
        """Set up mock client and user ID."""
        with patch.dict(os.environ, {
            "X_OAUTH2_SCOPES": TEST_SCOPES,
            "X_SCOPES": TEST_SCOPES,
        }), patch('x_client.get_client') as mock_get_client, \
             patch('x_client.get_my_user_id') as mock_get_id:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_get_id.return_value = "1234567890"
            
            # Mock successful unrepost
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.reposted = False
            client.users.unrepost_post.return_value = mock_response
            
            yield client, mock_get_id
    
    def test_unrepost_success(self, mock_setup, capsys):
        """Test that unrepost command undoes a repost."""
        client, mock_get_id = mock_setup
        
        args = MagicMock()
        args.id = "987654321"
        args.json = False
        
        cmd_unrepost(args)
        
        # Verify unrepost was called with correct parameters
        client.users.unrepost_post.assert_called_once_with(
            id="1234567890",
            source_tweet_id="987654321"
        )
        
        captured = capsys.readouterr()
        assert "undone successfully" in captured.out


class TestInteractionErrors:
    """Test error handling for interaction commands."""
    
    def test_like_handles_api_error(self, capsys):
        """Test that like command handles API errors gracefully."""
        with patch.dict(os.environ, {
            "X_OAUTH2_SCOPES": TEST_SCOPES,
            "X_SCOPES": TEST_SCOPES,
        }), patch('x_client.get_client') as mock_get_client, \
             patch('x_client.get_my_user_id') as mock_get_id:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_get_id.return_value = "1234567890"
            
            # Mock failed like
            mock_response = MagicMock()
            mock_response.data = None
            mock_response.errors = [{"detail": "Post not found"}]
            client.users.like_post.return_value = mock_response
            
            args = MagicMock()
            args.id = "999999999"
            args.json = False
            
            cmd_like(args)
            
            captured = capsys.readouterr()
            assert "Failed to like post" in captured.out


class TestInteractionOAuth2:
    """Test OAuth 2.0 authentication in interaction commands."""
    
    def test_interaction_uses_oauth2_user_context(self):
        """Test that interaction commands use OAuth 2.0 user-context auth."""
        with patch.dict(os.environ, {
            "X_OAUTH2_SCOPES": TEST_SCOPES,
            "X_SCOPES": TEST_SCOPES,
        }), patch('x_client.get_client') as mock_get_client, \
             patch('x_client.get_my_user_id') as mock_get_id:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_get_id.return_value = "1234567890"
            
            # Mock successful like response
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.liked = True
            client.users.like_post.return_value = mock_response
            
            args = MagicMock()
            args.id = "987654321"
            args.json = False
            
            cmd_like(args)
            
            # Verify get_client was called for user-context operations.
            assert mock_get_client.call_count >= 1
            for call in mock_get_client.call_args_list:
                if call[1]:
                    assert call[1].get('use_app_only', False) is False
