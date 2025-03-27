from beetsplug.beetstream import app
import hashlib
import os
from cryptography.fernet import Fernet
import json
from urllib.parse import unquote



def generate_key():
    return Fernet.generate_key()


def update_key(path, old_key, new_key):
    if not os.path.exists(path):
        return False

    data = load_credentials(path, old_key)
    if data is None:
        return False

    cipher = Fernet(new_key)
    reencrypted = cipher.encrypt(json.dumps(data).encode("utf-8"))

    # Write the encrypted data back to the file
    with open(path, "wb") as f:
        f.write(reencrypted)

    return True


def update_user(path, key, new_data):
    cipher = Fernet(key)

    if os.path.exists(path):
        data = load_credentials(path, key)
        if data is None:
            return False
    else:
        data = {}

    data.update(new_data)
    new_encrypted_users_data = cipher.encrypt(json.dumps(data).encode("utf-8"))

    # Write the encrypted data back to the file
    with open(path, "wb") as f:
        f.write(new_encrypted_users_data)

    return True


def load_credentials(path, key):
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        encrypted_data = f.read()
    try:
        cipher = Fernet(key)
        decrypted_data = cipher.decrypt(encrypted_data)
    except:
        print('Wrong key.')
        return None
    return json.loads(decrypted_data.decode('utf-8'))


def authenticate(req_values):
    user = unquote(req_values.get('u', ''))
    token = unquote(req_values.get('t', ''))
    salt = unquote(req_values.get('s', ''))
    clearpass = unquote(req_values.get('p', ''))

    if not user:
        app.logger.warning('No username provided.')
        return False

    if (not token or not salt) and not clearpass:
        app.logger.warning('No authentication data provided.')
        return False

    key = os.environ.get('BEETSTREAM_KEY', '')
    if not key:
        app.logger.warning('Decryption key not found.')
        return False

    users_data = load_credentials(app.config['users_storage'], key)
    if not users_data:
        app.logger.warning("Can't load saved users.")
        return False

    if user and token and salt:
        pw_digest = hashlib.md5(f'{users_data.get(user, '')}{salt}'.encode('utf-8')).hexdigest().lower()
        is_auth = token == pw_digest
    elif clearpass:
        pw = clearpass.lstrip('enc:')
        is_auth = pw == users_data.get(user, '')
    else:
        is_auth = False

    print(f"User {user} {'is' if is_auth else 'isn\'t'} authenticated.")
    return is_auth