from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import Schema, fields, validate, ValidationError
from werkzeug.utils import secure_filename
import os
from datetime import datetime
import logging

from auth.utils import merchant_role_required
from common.decorators import rate_limit, cache_response
from auth.models import User, MerchantProfile
from auth.models.merchant_document import VerificationStatus, DocumentType, MerchantDocument
from auth.models.country_config import CountryConfig, CountryCode
from common.database import db

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Schema definitions
class CreateMerchantProfileSchema(Schema):
    business_name = fields.Str(required=True, validate=validate.Length(min=2, max=200))
    business_description = fields.Str(required=True)
    business_email = fields.Email(required=True)
    business_phone = fields.Str(required=True)
    business_address = fields.Str(required=True)
    country_code = fields.Str(required=True, validate=validate.OneOf([code.value for code in CountryCode]))
    
    # Common fields
    bank_account_number = fields.Str(validate=validate.Length(min=9, max=18))
    bank_name = fields.Str(validate=validate.Length(max=100))
    bank_branch = fields.Str(validate=validate.Length(max=100))
    bank_iban = fields.Str(validate=validate.Length(max=34))

    # India-specific fields
    gstin = fields.Str(validate=validate.Length(max=15))
    pan_number = fields.Str(validate=validate.Length(max=10))
    bank_ifsc_code = fields.Str(validate=validate.Length(min=11, max=11))

    # Global fields
    tax_id = fields.Str(validate=validate.Length(max=50))
    vat_number = fields.Str(validate=validate.Length(max=50))
    sales_tax_number = fields.Str(validate=validate.Length(max=50))
    bank_swift_code = fields.Str(validate=validate.Length(max=11))
    bank_routing_number = fields.Str(validate=validate.Length(max=20))

class UpdateProfileSchema(Schema):
    business_name = fields.Str(validate=validate.Length(min=2, max=200))
    business_description = fields.Str()
    business_email = fields.Email()
    business_phone = fields.Str()
    business_address = fields.Str()
    
    # Country and Region Information
    country_code = fields.Str(validate=validate.OneOf([code.value for code in CountryCode]))
    state_province = fields.Str()
    city = fields.Str()
    postal_code = fields.Str()
    
    # Common fields
    bank_account_number = fields.Str(validate=validate.Length(min=9, max=18))
    bank_name = fields.Str(validate=validate.Length(max=100))
    bank_branch = fields.Str(validate=validate.Length(max=100))
    bank_iban = fields.Str(validate=validate.Length(max=34))
    
    # India-specific fields
    gstin = fields.Str(validate=validate.Length(max=15))
    pan_number = fields.Str(validate=validate.Length(max=10))
    bank_ifsc_code = fields.Str(validate=validate.Length(max=11))
    
    # Global fields
    tax_id = fields.Str(validate=validate.Length(max=50))
    vat_number = fields.Str(validate=validate.Length(max=50))
    sales_tax_number = fields.Str(validate=validate.Length(max=50))
    bank_swift_code = fields.Str(validate=validate.Length(max=11))
    bank_routing_number = fields.Str(validate=validate.Length(max=20))

    def validate(self, data, **kwargs):
        """Custom validation based on country code."""
        errors = {}
        country_code = data.get('country_code')

        if country_code == CountryCode.INDIA.value:
            # Validate Indian-specific fields
            if data.get('bank_ifsc_code') and len(data['bank_ifsc_code']) != 11:
                errors['bank_ifsc_code'] = ['Length must be between 11 and 11.']
            if data.get('gstin') and len(data['gstin']) > 15:
                errors['gstin'] = ['Length must be between 0 and 15.']
            if data.get('pan_number') and len(data['pan_number']) > 10:
                errors['pan_number'] = ['Length must be between 0 and 10.']
        else:  # GLOBAL
            # Validate Global-specific fields
            if data.get('bank_swift_code') and len(data['bank_swift_code']) > 11:
                errors['bank_swift_code'] = ['Length must be between 0 and 11.']
            if data.get('tax_id') and len(data['tax_id']) > 50:
                errors['tax_id'] = ['Length must be between 0 and 50.']
            if data.get('vat_number') and len(data['vat_number']) > 50:
                errors['vat_number'] = ['Length must be between 0 and 50.']
            if data.get('sales_tax_number') and len(data['sales_tax_number']) > 50:
                errors['sales_tax_number'] = ['Length must be between 0 and 50.']

        if errors:
            raise ValidationError(errors)

        return data

# Create merchants blueprint
merchants_bp = Blueprint('merchants', __name__)

@merchants_bp.route('/profile', methods=['POST'])
@jwt_required()
@merchant_role_required
def create_profile():
    """Create initial merchant profile."""
    try:
        # Validate request data
        schema = CreateMerchantProfileSchema()
        data = schema.load(request.json)
        
        merchant_id = get_jwt_identity()
        
        # Check if profile already exists
        existing_profile = MerchantProfile.get_by_user_id(merchant_id)
        if existing_profile:
            return jsonify({"error": "Merchant profile already exists"}), 400
        
        # Validate country-specific fields
        country_code = data.get('country_code')
        if country_code == CountryCode.INDIA.value:
            if not data.get('gstin') or not data.get('pan_number') or not data.get('bank_ifsc_code'):
                return jsonify({
                    "error": "Validation error",
                    "details": "GSTIN, PAN number, and IFSC code are required for Indian merchants"
                }), 400
        else:  # Global
            if not data.get('tax_id') or not data.get('bank_swift_code'):
                return jsonify({
                    "error": "Validation error",
                    "details": "Tax ID and SWIFT code are required for international merchants"
                }), 400
        
        # Create new merchant profile
        merchant_profile = MerchantProfile(
            user_id=merchant_id,
            business_name=data['business_name'],
            business_description=data['business_description'],
            business_email=data['business_email'],
            business_phone=data['business_phone'],
            business_address=data['business_address'],
            country_code=country_code,
            # India-specific fields
            gstin=data.get('gstin'),
            pan_number=data.get('pan_number'),
            bank_ifsc_code=data.get('bank_ifsc_code'),
            # Global fields
            tax_id=data.get('tax_id'),
            vat_number=data.get('vat_number'),
            sales_tax_number=data.get('sales_tax_number'),
            bank_swift_code=data.get('bank_swift_code'),
            bank_routing_number=data.get('bank_routing_number'),
            # Common fields
            bank_account_number=data.get('bank_account_number'),
            bank_name=data.get('bank_name'),
            bank_branch=data.get('bank_branch'),
            bank_iban=data.get('bank_iban'),
            verification_status=VerificationStatus.PENDING,
            is_verified=False
        )
        
        merchant_profile.save()
        
        return jsonify({
            "message": "Merchant profile created successfully",
            "profile": {
                "business_name": merchant_profile.business_name,
                "business_email": merchant_profile.business_email,
                "country_code": merchant_profile.country_code,
                "verification_status": merchant_profile.verification_status.value
            }
        }), 201
        
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@merchants_bp.route('/profile', methods=['GET'])
@jwt_required()
@merchant_role_required
@cache_response(timeout=60, key_prefix='merchant_profile')
def get_profile():
    """Get merchant profile."""
    merchant_id = get_jwt_identity()
    merchant_profile = MerchantProfile.get_by_user_id(merchant_id)
    
    if not merchant_profile:
        return jsonify({"error": "Merchant profile not found"}), 404
    
    return jsonify({
        "profile": {
            "business_name": merchant_profile.business_name,
            "business_description": merchant_profile.business_description,
            "business_email": merchant_profile.business_email,
            "business_phone": merchant_profile.business_phone,
            "business_address": merchant_profile.business_address,
            "country_code": merchant_profile.country_code,
            "state_province": merchant_profile.state_province,
            "city": merchant_profile.city,
            "postal_code": merchant_profile.postal_code,
            "gstin": merchant_profile.gstin,
            "pan_number": merchant_profile.pan_number,
            "tax_id": merchant_profile.tax_id,
            "vat_number": merchant_profile.vat_number,
            "sales_tax_number": merchant_profile.sales_tax_number,
            "bank_account_number": merchant_profile.bank_account_number,
            "bank_name": merchant_profile.bank_name,
            "bank_branch": merchant_profile.bank_branch,
            "bank_ifsc_code": merchant_profile.bank_ifsc_code,
            "bank_swift_code": merchant_profile.bank_swift_code,
            "bank_routing_number": merchant_profile.bank_routing_number,
            "bank_iban": merchant_profile.bank_iban,
            "is_verified": merchant_profile.is_verified,
            "verification_status": merchant_profile.verification_status.value,
            "verification_submitted_at": merchant_profile.verification_submitted_at.isoformat() if merchant_profile.verification_submitted_at else None,
            "verification_completed_at": merchant_profile.verification_completed_at.isoformat() if merchant_profile.verification_completed_at else None,
            "verification_notes": merchant_profile.verification_notes,
            "required_documents": merchant_profile.required_documents,
            "submitted_documents": merchant_profile.submitted_documents
        }
    }), 200

@merchants_bp.route('/profile', methods=['PUT'])
@jwt_required()
@merchant_role_required
def update_profile():
    """Update merchant profile."""
    try:
        logger.debug(f"Received profile update request: {request.json}")
        
        # Validate request data
        schema = UpdateProfileSchema()
        try:
            data = schema.load(request.json)
            logger.debug(f"Validated data: {data}")
        except ValidationError as e:
            logger.error(f"Schema validation error: {e.messages}")
            return jsonify({"error": "Validation error", "details": e.messages}), 400
        
        merchant_id = get_jwt_identity()
        merchant_profile = MerchantProfile.get_by_user_id(merchant_id)
        
        if not merchant_profile:
            logger.error(f"Merchant profile not found for user_id: {merchant_id}")
            return jsonify({"error": "Merchant profile not found"}), 404
        
        # Update profile fields
        for field, value in data.items():
            if hasattr(merchant_profile, field):
                setattr(merchant_profile, field, value)
                logger.debug(f"Updated field {field} to {value}")
        
        # If country code is updated, validate required fields
        if 'country_code' in data:
            country_code = data['country_code']
            logger.debug(f"Validating country-specific fields for country: {country_code}")
            
            if country_code == CountryCode.INDIA.value:
                missing_fields = []
                if not merchant_profile.gstin:
                    missing_fields.append('gstin')
                if not merchant_profile.pan_number:
                    missing_fields.append('pan_number')
                if not merchant_profile.bank_ifsc_code:
                    missing_fields.append('bank_ifsc_code')
                
                if missing_fields:
                    error_msg = f"Missing required fields for Indian merchant: {', '.join(missing_fields)}"
                    logger.error(error_msg)
                    return jsonify({
                        "error": "Validation error",
                        "details": error_msg
                    }), 400
            else:  # Global
                missing_fields = []
                if not merchant_profile.tax_id:
                    missing_fields.append('tax_id')
                if not merchant_profile.bank_swift_code:
                    missing_fields.append('bank_swift_code')
                
                if missing_fields:
                    error_msg = f"Missing required fields for international merchant: {', '.join(missing_fields)}"
                    logger.error(error_msg)
                    return jsonify({
                        "error": "Validation error",
                        "details": error_msg
                    }), 400
        
        merchant_profile.updated_at = datetime.utcnow()
        db.session.commit()
        logger.info(f"Successfully updated profile for merchant_id: {merchant_id}")
        
        return jsonify({
            "message": "Profile updated successfully",
            "profile": {
                "business_name": merchant_profile.business_name,
                "business_email": merchant_profile.business_email,
                "country_code": merchant_profile.country_code,
                "verification_status": merchant_profile.verification_status.value
            }
        }), 200
        
    except ValidationError as e:
        logger.error(f"Validation error: {e.messages}")
        return jsonify({"error": "Validation error", "details": e.messages}), 400
    except Exception as e:
        logger.error(f"Unexpected error in update_profile: {str(e)}", exc_info=True)
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@merchants_bp.route('/profile/verify', methods=['POST'])
@jwt_required()
@merchant_role_required
def submit_for_verification():
    """Submit merchant profile for verification."""
    try:
        merchant_id = get_jwt_identity()
        merchant_profile = MerchantProfile.get_by_user_id(merchant_id)
        
        if not merchant_profile:
            return jsonify({"error": "Merchant profile not found"}), 404
            
        # Validate required fields based on country
        if merchant_profile.country_code == CountryCode.INDIA.value:
            if not merchant_profile.gstin or not merchant_profile.pan_number or not merchant_profile.bank_ifsc_code:
                return jsonify({
                    "error": "Validation error",
                    "details": "GSTIN, PAN number, and IFSC code are required for Indian merchants"
                }), 400
        else:  # Global
            if not merchant_profile.tax_id or not merchant_profile.bank_swift_code:
                return jsonify({
                    "error": "Validation error",
                    "details": "Tax ID and SWIFT code are required for international merchants"
                }), 400
            
        merchant_profile.submit_for_verification()
        
        return jsonify({
            "message": "Profile submitted for verification",
            "verification_status": merchant_profile.verification_status.value
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@merchants_bp.route('/products', methods=['GET'])
@jwt_required()
@merchant_role_required
@cache_response(timeout=60, key_prefix='merchant_products')
def get_products():
    """Get merchant products."""
    merchant_id = get_jwt_identity()
    return {"message": f"Products for merchant ID: {merchant_id}"}, 200

@merchants_bp.route('/analytics', methods=['GET'])
@jwt_required()
@merchant_role_required
@cache_response(timeout=300, key_prefix='merchant_analytics')
def get_analytics():
    """Get merchant analytics."""
    merchant_id = get_jwt_identity()
    return {"message": f"Analytics for merchant ID: {merchant_id}"}, 200

@merchants_bp.route('/verification-status', methods=['GET'])
@jwt_required()
@merchant_role_required
def get_verification_status():
    """Get merchant verification status and check if documents have been submitted."""
    try:
        merchant_id = get_jwt_identity()
        merchant_profile = MerchantProfile.get_by_user_id(merchant_id)
        
        if not merchant_profile:
            return jsonify({
                "error": "Merchant profile not found",
                "has_submitted_documents": False
            }), 404
        
        # Get all documents for the merchant
        documents = MerchantDocument.get_by_merchant_id(merchant_profile.id)
        
        # Check if documents have been submitted
        has_submitted_documents = (
            merchant_profile.verification_status != VerificationStatus.PENDING or
            merchant_profile.verification_submitted_at is not None
        )
        
        # Prepare document details with admin notes
        document_details = [{
            'document_type': doc.document_type.value,
            'status': doc.status.value,
            'admin_notes': doc.admin_notes,
            'verified_at': doc.verified_at.isoformat() if doc.verified_at else None
        } for doc in documents]
        
        return jsonify({
            "has_submitted_documents": has_submitted_documents,
            "verification_status": merchant_profile.verification_status.value,
            "verification_submitted_at": merchant_profile.verification_submitted_at.isoformat() if merchant_profile.verification_submitted_at else None,
            "verification_completed_at": merchant_profile.verification_completed_at.isoformat() if merchant_profile.verification_completed_at else None,
            "verification_notes": merchant_profile.verification_notes,
            "required_documents": merchant_profile.required_documents,
            "submitted_documents": merchant_profile.submitted_documents,
            "document_details": document_details
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting verification status: {str(e)}", exc_info=True)
        return jsonify({
            "error": "Failed to get verification status",
            "details": str(e),
            "has_submitted_documents": False
        }), 500