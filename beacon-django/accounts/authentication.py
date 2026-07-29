from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed

class SafeJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        try:
            user = super().get_user(validated_token)
        except AuthenticationFailed as e:
            # Check if the exception message indicates that the user is inactive
            detail_str = str(getattr(e, 'detail', ''))
            if 'User is inactive' in detail_str or 'inactive' in detail_str.lower():
                raise AuthenticationFailed(
                    'Your account is inactive. Please contact the administrator.',
                    code='user_inactive'
                )
            raise e
        
        if not user.is_active:
            raise AuthenticationFailed(
                'Your account is inactive. Please contact the administrator.',
                code='user_inactive'
            )
        return user
