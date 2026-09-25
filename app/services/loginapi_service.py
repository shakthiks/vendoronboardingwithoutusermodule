import bcrypt
from app.db.base import get_secondary_connection
from app.core.config import settings

DB_SCHEMA = settings.DB_SCHEMA
USERS_TABLE = f"[{DB_SCHEMA}].[HIQ_Users]"
ROLES_TABLE = f"[{DB_SCHEMA}].[HIQ_Roles]"
USER_ROLES_TABLE = f"[{DB_SCHEMA}].[HIQ_UserRoles]"


def login_user(payload):
    with get_secondary_connection() as conn:
        cursor = conn.cursor()
        try:
            # Get user details
            cursor.execute(f"""
                SELECT UserId,
                       Email,
                       PasswordHash,
                       IsActive,
                       Username,
                       FullName
                FROM {USERS_TABLE}
                WHERE Email = ?
            """, (payload.Email,))
            user = cursor.fetchone()

            if not user:
                return {
                    "status": False,
                    "message": "Invalid Email or Password"
                }

            user_id = user.UserId
            hashed_password = user.PasswordHash
            is_active = user.IsActive

            if not is_active:
                return {
                    "status": False,
                    "message": "User is inactive"
                }

            if not bcrypt.checkpw(
                payload.Password.encode("utf-8"),
                hashed_password.encode("utf-8")
            ):
                return {
                    "status": False,
                    "message": "Invalid Email or Password"
                }

            # Fetch Role
            cursor.execute(f"""
                SELECT
                    ur.RoleId,
                    r.RoleName
                FROM {USER_ROLES_TABLE} ur
                INNER JOIN {ROLES_TABLE} r
                    ON ur.RoleId = r.RoleId
                WHERE ur.UserId = ?
            """, (user_id,))
            role = cursor.fetchone()

            if not role:
                return {
                    "status": False,
                    "message": "No role assigned to this user"
                }

            return {
                "status": True,
                "message": "Login Successful",
                "data": {
                    "UserId": user_id,
                    "RoleId": role.RoleId,
                    "RoleName": role.RoleName,
                    "Email": user.Email,
                    "Username": user.Username,
                    "FullName": user.FullName
                }
            }

        except Exception as e:
            # Log the real error server-side for debugging
            # logger.error(f"login_user error: {e}")
            return {
                "status": False,
                "message": "An error occurred while processing your request. Please try again."
            }