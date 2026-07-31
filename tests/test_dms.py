"""
Tests for DM (Direct Message) commands: dm-send, dm-list, dm-get
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys
import os

sys.path.insert(0, str(Path(__file__).parent.parent))

from x_client import cmd_dm_send, cmd_dm_list, cmd_dm_get

TEST_SCOPES = "tweet.read tweet.write dm.write offline.access users.read"


def create_mock_message(msg_id, text, sender_id="12345", created_at="2024-01-01T12:00:00Z"):
    """Helper to create a mock message object."""
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.sender_id = sender_id
    msg.created_at = created_at
    return msg


def create_mock_conversation(conv_id, participants=None):
    """Helper to create a mock conversation object."""
    conv = MagicMock()
    conv.id = conv_id
    conv.participants = participants or []
    return conv


class TestDmSendCommand:
    """Tests for the 'dm-send' command."""
    
    @pytest.fixture
    def mock_setup(self):
        """Set up mock client."""
        with patch.dict(os.environ, {
            "X_OAUTH2_SCOPES": TEST_SCOPES,
            "X_SCOPES": TEST_SCOPES,
        }), patch('x_client.get_client') as mock_get_client, \
             patch('x_client.resolve_user_id') as mock_resolve:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_resolve.return_value = "987654321"
            
            # Mock successful message send
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.id = "msg_123456"
            client.dm.send_message.return_value = mock_response
            
            yield client, mock_resolve
    
    def test_dm_send_to_user(self, mock_setup, capsys):
        """Test sending a DM to a user."""
        client, mock_resolve = mock_setup
        
        args = MagicMock()
        args.user = "@testuser"
        args.conversation = None
        args.text = "Hello, this is a test message!"
        args.json = False
        
        cmd_dm_send(args)
        
        # Verify user was resolved
        mock_resolve.assert_called_once_with(client, "@testuser")
        
        # Verify message was sent
        client.dm.send_message.assert_called_once()
        call_kwargs = client.dm.send_message.call_args[1]
        assert call_kwargs['participant_id'] == "987654321"
        assert call_kwargs['body']['text'] == "Hello, this is a test message!"
        
        captured = capsys.readouterr()
        assert "Message sent successfully" in captured.out
    
    def test_dm_send_to_conversation(self, mock_setup, capsys):
        """Test sending a DM to an existing conversation."""
        client, mock_resolve = mock_setup
        
        args = MagicMock()
        args.user = None
        args.conversation = "conv_123456"
        args.text = "Reply in conversation"
        args.json = False
        
        cmd_dm_send(args)
        
        # Verify user was NOT resolved (using conversation directly)
        mock_resolve.assert_not_called()
        
        # Verify message was sent to conversation
        client.dm.send_message.assert_called_once()
        call_kwargs = client.dm.send_message.call_args[1]
        assert call_kwargs['participant_id'] == "conv_123456"
        
        captured = capsys.readouterr()
        assert "Message sent successfully" in captured.out
    
    def test_dm_send_with_user_id(self, mock_setup, capsys):
        """Test sending a DM using a numeric user ID."""
        client, mock_resolve = mock_setup
        mock_resolve.return_value = "123456789"
        
        args = MagicMock()
        args.user = "123456789"  # Numeric ID
        args.conversation = None
        args.text = "Message to user by ID"
        args.json = False
        
        cmd_dm_send(args)
        
        # Verify resolve was called with the ID
        mock_resolve.assert_called_once_with(client, "123456789")
        
        captured = capsys.readouterr()
        assert "Message sent successfully" in captured.out
    
    def test_dm_send_missing_recipient(self, mock_setup, capsys):
        """Test that dm-send requires either --user or --conversation."""
        client, mock_resolve = mock_setup
        
        args = MagicMock()
        args.user = None
        args.conversation = None
        args.text = "Test message"
        
        with pytest.raises(SystemExit):
            cmd_dm_send(args)


class TestDmListCommand:
    """Tests for the 'dm-list' command."""
    
    @pytest.fixture
    def mock_setup(self):
        """Set up mock client."""
        with patch.dict(os.environ, {
            "X_OAUTH2_SCOPES": TEST_SCOPES,
            "X_SCOPES": TEST_SCOPES,
        }), patch('x_client.get_client') as mock_get_client:
            
            client = MagicMock()
            mock_get_client.return_value = client
            
            # Mock conversations
            mock_conversations = [
                create_mock_conversation("conv_1", [
                    MagicMock(username="user1", id="111"),
                    MagicMock(username="user2", id="222")
                ]),
                create_mock_conversation("conv_2", [
                    MagicMock(username="user3", id="333")
                ])
            ]
            
            mock_page = MagicMock()
            mock_page.data = mock_conversations
            client.dm.get_conversations.return_value = iter([mock_page])
            
            yield client
    
    def test_dm_list_retrieves_conversations(self, mock_setup, capsys):
        """Test that dm-list retrieves conversations."""
        client = mock_setup
        
        args = MagicMock()
        args.limit = 10
        args.json = False
        
        cmd_dm_list(args)
        
        # Verify get_conversations was called
        client.dm.get_conversations.assert_called_once()
        
        captured = capsys.readouterr()
        assert "DM Conversations" in captured.out
        assert "conv_1" in captured.out
        assert "conv_2" in captured.out
    
    def test_dm_list_respects_limit(self, mock_setup):
        """Test that dm-list respects the limit parameter."""
        client = mock_setup
        
        args = MagicMock()
        args.limit = 5
        args.json = False
        
        cmd_dm_list(args)
        
        # Verify get_conversations was called with correct max_results
        client.dm.get_conversations.assert_called_once()
        call_kwargs = client.dm.get_conversations.call_args[1]
        assert call_kwargs['max_results'] == 5


class TestDmGetCommand:
    """Tests for the 'dm-get' command."""
    
    @pytest.fixture
    def mock_setup(self):
        """Set up mock client."""
        with patch.dict(os.environ, {
            "X_OAUTH2_SCOPES": TEST_SCOPES,
            "X_SCOPES": TEST_SCOPES,
        }), patch('x_client.get_client') as mock_get_client:
            
            client = MagicMock()
            mock_get_client.return_value = client
            
            # Mock messages
            mock_messages = [
                create_mock_message("msg_1", "Hello there!", "111", "2024-01-01T10:00:00Z"),
                create_mock_message("msg_2", "How are you?", "222", "2024-01-01T10:05:00Z"),
            ]
            
            mock_page = MagicMock()
            mock_page.data = mock_messages
            client.dm.get_messages.return_value = iter([mock_page])
            
            yield client
    
    def test_dm_get_retrieves_messages(self, mock_setup, capsys):
        """Test that dm-get retrieves messages from a conversation."""
        client = mock_setup
        
        args = MagicMock()
        args.conversation = "conv_123456"
        args.limit = 20
        args.json = False
        
        cmd_dm_get(args)
        
        # Verify get_messages was called with correct conversation_id
        client.dm.get_messages.assert_called_once()
        call_kwargs = client.dm.get_messages.call_args[1]
        assert call_kwargs['conversation_id'] == "conv_123456"
        
        captured = capsys.readouterr()
        assert "Messages in conversation conv_123456" in captured.out
        assert "Hello there!" in captured.out
        assert "How are you?" in captured.out
    
    def test_dm_get_respects_limit(self, mock_setup):
        """Test that dm-get respects the limit parameter."""
        client = mock_setup
        
        args = MagicMock()
        args.conversation = "conv_123456"
        args.limit = 10
        args.json = False
        
        cmd_dm_get(args)
        
        # Verify get_messages was called with correct max_results
        client.dm.get_messages.assert_called_once()
        call_kwargs = client.dm.get_messages.call_args[1]
        assert call_kwargs['max_results'] == 10


class TestResolveUserId:
    """Tests for the resolve_user_id helper function."""
    
    def test_resolve_numeric_id(self):
        """Test that numeric strings are returned as-is."""
        client = MagicMock()
        
        from x_client import resolve_user_id
        
        result = resolve_user_id(client, "123456789")
        assert result == "123456789"
        
        # Verify no API call was made
        client.users.get_by_username.assert_not_called()
    
    def test_resolve_username(self):
        """Test that usernames are resolved to IDs."""
        client = MagicMock()
        
        mock_response = MagicMock()
        mock_response.data = MagicMock()
        mock_response.data.id = "987654321"
        client.users.get_by_username.return_value = mock_response
        
        from x_client import resolve_user_id
        
        result = resolve_user_id(client, "@testuser")
        
        # Verify API call was made
        client.users.get_by_username.assert_called_once_with(username="testuser")
        assert result == "987654321"
    
    def test_resolve_username_not_found(self):
        """Test that missing users raise an error."""
        client = MagicMock()
        client.users.get_by_username.return_value = None
        
        from x_client import resolve_user_id
        
        with pytest.raises(ValueError, match="User '@nonexistent' not found"):
            resolve_user_id(client, "@nonexistent")


class TestDmOAuth2:
    """Test OAuth 2.0 authentication requirements for DM commands."""
    
    def test_dm_commands_require_user_context(self):
        """Test that DM commands use OAuth 2.0 user-context auth."""
        with patch.dict(os.environ, {
            "X_OAUTH2_SCOPES": TEST_SCOPES,
            "X_SCOPES": TEST_SCOPES,
        }), patch('x_client.get_client') as mock_get_client, \
             patch('x_client.resolve_user_id') as mock_resolve:
            
            client = MagicMock()
            mock_get_client.return_value = client
            mock_resolve.return_value = "123456789"
            
            # Mock successful message send
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.id = "msg_123"
            client.dm.send_message.return_value = mock_response
            
            args = MagicMock()
            args.user = "@testuser"
            args.conversation = None
            args.text = "Test message"
            args.json = False
            
            cmd_dm_send(args)
            
            # Verify get_client was called for user-context operations.
            assert mock_get_client.call_count >= 1
            for call in mock_get_client.call_args_list:
                if call[1]:
                    assert call[1].get('use_app_only', False) is False
