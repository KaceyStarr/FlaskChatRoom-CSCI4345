let socket = io();
let currentRoom = 'General';
let username = document.getElementById('username').textContent;
let roomMessages = {};

// Socket event listeners
socket.on('connect', () => {
	joinRoom('General');
	highlightActiveRoom('General');
});

socket.on('message', (data) => {
	addMessage(
		data.username,
		data.msg,
		data.username === username ? 'own' : 'other'
	);
});

socket.on('private_message', (data) => {
	addMessage(data.from, `[Private] ${data.msg}`, 'private');
});

socket.on('status', (data) => {
	addMessage('System', data.msg, 'system');
});

socket.on('active_users', (data) => {
	const userList = document.getElementById('active-users');
	userList.innerHTML = data.users
		.map(
			(user) => `
            <div class="user-item" onclick="insertPrivateMessage('${user}')">
                ${user} ${user === username ? '(you)' : ''}
            </div>
        `
		)
		.join('');
});

// 🆕 Load chat history on join
socket.on('chat_history', (data) => {
	const chat = document.getElementById('chat');
	chat.innerHTML = ''; // Clear current chat UI

	roomMessages[currentRoom] = []; // Reset local store

	data.messages.forEach((msg) => {
		const sender = msg.username;
		const message = msg.message;
		const type = sender === username ? 'own' : 'other';

		roomMessages[currentRoom].push({ sender, message, type });
		addMessage(sender, message, type);
	});
});

// Message handling
function addMessage(sender, message, type) {
	if (!roomMessages[currentRoom]) {
		roomMessages[currentRoom] = [];
	}
	roomMessages[currentRoom].push({ sender, message, type });

	const chat = document.getElementById('chat');
	const messageDiv = document.createElement('div');
	messageDiv.className = `message ${type}`;
	messageDiv.textContent = `${sender}: ${message}`;

	chat.appendChild(messageDiv);
	chat.scrollTop = chat.scrollHeight;
}

function sendMessage() {
	const input = document.getElementById('message');
	const message = input.value.trim();

	if (!message) return;

	if (message.startsWith('@')) {
		// Send private message
		const [target, ...msgParts] = message.substring(1).split(' ');
		const privateMsg = msgParts.join(' ');

		if (privateMsg) {
			socket.emit('message', {
				msg: privateMsg,
				type: 'private',
				target: target,
			});
		}
	} else {
		// Send room message
		socket.emit('message', {
			msg: message,
			room: currentRoom,
		});
	}

	input.value = '';
	input.focus();
}

function joinRoom(room) {
	socket.emit('leave', { room: currentRoom });
	currentRoom = room;
	socket.emit('join', { room });

	highlightActiveRoom(room);

	// Clear current chat — history will be reloaded via chat_history event
	const chat = document.getElementById('chat');
	chat.innerHTML = '';
}

function insertPrivateMessage(user) {
	document.getElementById('message').value = `@${user} `;
	document.getElementById('message').focus();
}

function handleKeyPress(event) {
	if (event.key === 'Enter' && !event.shiftKey) {
		event.preventDefault();
		sendMessage();
	}
}

// Initialize chat when page loads
document.addEventListener('DOMContentLoaded', () => {
	if ('Notification' in window) {
		Notification.requestPermission();
	}
});

// Highlight selected room
function highlightActiveRoom(room) {
	document.querySelectorAll('.room-item').forEach((item) => {
		item.classList.remove('active-room');
		if (item.textContent.trim() === room) {
			item.classList.add('active-room');
		}
	});
}
