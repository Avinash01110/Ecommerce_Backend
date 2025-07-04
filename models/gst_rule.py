from datetime import datetime, timezone, date as DDate 
from common.database import db, BaseModel
from models.enums import ProductPriceConditionType
from models.category import Category 
from sqlalchemy import or_, and_, desc
from decimal import Decimal, InvalidOperation

class GSTRule(BaseModel):
    _tablename_ = 'gst_rules'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

    category_id = db.Column(db.Integer, db.ForeignKey('categories.category_id'), nullable=False, index=True) # Made NOT NULL
    
    price_condition_type = db.Column(db.Enum(ProductPriceConditionType), nullable=False, default=ProductPriceConditionType.ANY)
    price_condition_value = db.Column(db.Numeric(10, 2), nullable=True) # e.g., 1000.00
    
    gst_rate_percentage = db.Column(db.Numeric(5, 2), nullable=False) # e.g., 5.00 for 5%

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)

    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # created_at and updated_at are inherited from BaseModel

    # Relationships
    category = db.relationship('Category', foreign_keys=[category_id])
    creator = db.relationship('User', foreign_keys=[created_by])
    updater = db.relationship('User', foreign_keys=[updated_by])

    def _repr_(self):
        return f"<GSTRule id={self.id} name='{self.name}' category_id='{self.category_id}' rate='{self.gst_rate_percentage}%'>"

    def serialize(self):
        return {
            "id": self.id,
            "name": self.name,
            
            "category_id": self.category_id,
            "category_name": self.category.name if self.category else "N/A",
            "price_condition_type": self.price_condition_type.value,
            "price_condition_value": str(self.price_condition_value) if self.price_condition_value is not None else None,
            "gst_rate_percentage": str(self.gst_rate_percentage),
            "is_active": self.is_active,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @staticmethod
    def _get_category_lineage_ids(category_id, db_session):
        """Helper to get category lineage (current, parent, grandparent, etc.)
           Returns an ordered list: [specific_cat_id, parent_id, grandparent_id, ...]
        """
        if not category_id:
            return []
        
        lineage_ids = []
        current_cat_id = category_id
        
        max_depth = 10 
        count = 0

        while current_cat_id and count < max_depth:
            lineage_ids.append(current_cat_id)
            category_obj = db_session.query(Category.parent_id).filter(Category.category_id == current_cat_id).first()
            if category_obj:
                current_cat_id = category_obj.parent_id
            else: # Should not happen if data is consistent, but good to break
                current_cat_id = None
            count += 1
        return lineage_ids

    @staticmethod
    def find_applicable_rule(db_session, product_category_id, product_inclusive_price: Decimal): # price is now inclusive
        today = DDate.today()
        
        try:
            inclusive_price_decimal = Decimal(product_inclusive_price)
        except (TypeError, InvalidOperation):
            return None # Cannot determine rule without a valid price for comparison

        category_lineage_ids = GSTRule._get_category_lineage_ids(product_category_id, db_session)
        if not category_lineage_ids:
            return None

        all_active_rules = db_session.query(GSTRule).filter(
            GSTRule.is_active == True,
            GSTRule.category_id.in_(category_lineage_ids),
            or_(GSTRule.start_date == None, GSTRule.start_date <= today),
            or_(GSTRule.end_date == None, GSTRule.end_date >= today)
        ).order_by(
            desc(GSTRule.id) 
        ).all()

        if not all_active_rules:
            return None

        best_rule_for_product = None
        for current_lineage_cat_id in category_lineage_ids:
            rules_for_this_category_level = [
                rule for rule in all_active_rules if rule.category_id == current_lineage_cat_id
            ]

            if not rules_for_this_category_level:
                continue

            matched_rules_at_this_level = []
            for rule in rules_for_this_category_level:
                price_condition_met = False
                if rule.price_condition_type == ProductPriceConditionType.ANY:
                    price_condition_met = True
                elif rule.price_condition_value is not None:
                    # Price condition in rule now refers to INCLUSIVE price
                    rule_price_val_inclusive = Decimal(rule.price_condition_value) 
                    
                    if rule.price_condition_type == ProductPriceConditionType.LESS_THAN and inclusive_price_decimal < rule_price_val_inclusive: price_condition_met = True
                    elif rule.price_condition_type == ProductPriceConditionType.LESS_THAN_OR_EQUAL_TO and inclusive_price_decimal <= rule_price_val_inclusive: price_condition_met = True
                    elif rule.price_condition_type == ProductPriceConditionType.GREATER_THAN and inclusive_price_decimal > rule_price_val_inclusive: price_condition_met = True
                    elif rule.price_condition_type == ProductPriceConditionType.GREATER_THAN_OR_EQUAL_TO and inclusive_price_decimal >= rule_price_val_inclusive: price_condition_met = True
                    elif rule.price_condition_type == ProductPriceConditionType.EQUAL_TO and inclusive_price_decimal == rule_price_val_inclusive: price_condition_met = True
                
                if price_condition_met:
                    matched_rules_at_this_level.append(rule)
            
            if matched_rules_at_this_level:
                specific_price_rules = [r for r in matched_rules_at_this_level if r.price_condition_type != ProductPriceConditionType.ANY]
                any_price_rules = [r for r in matched_rules_at_this_level if r.price_condition_type == ProductPriceConditionType.ANY]

                if specific_price_rules:
                    best_rule_for_product = specific_price_rules[0] # Already sorted by ID desc
                    return best_rule_for_product 
                elif any_price_rules:
                    best_rule_for_product = any_price_rules[0] # Already sorted by ID desc
                    return best_rule_for_product
        
        return None